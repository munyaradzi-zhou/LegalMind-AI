# LegalMind runbook

What to do when an alert fires. Alerts are defined in [`infra/modules/alerts.bicep`](../infra/modules/alerts.bicep) and email the action group `ag-legalmind-<env>`. The **LegalMind operations** workbook (Azure portal → Monitor → Workbooks, or the Application Insights resource → Workbooks) shows traffic, latency per stage, grounding labels, tokens and cost, and failing dependencies.

Run the KQL in Application Insights → Logs (`appi-legalmind-<env>`). Names below use `<env>` = `dev`, `staging` or `prod`, and the resource group `rg-legalmind-<env>`.

## First five minutes, for any alert

1. Open the alert email's link: which rule, since when, and the values it saw.
2. Open the workbook for the last hour: is traffic normal, which stage is slow, which dependency fails?
3. Check whether something changed: `az containerapp revision list -g rg-legalmind-<env> -n ca-legalmind-api-<env> -o table` (a new revision in the last hour?) and the last CD run (`gh run list --workflow cd.yml --limit 3`).
4. If the last deploy is the likely cause, roll back first and investigate after (below).

## Roll back the API

The apps run one revision at a time. Redeploy the previous image:

```bash
az containerapp revision list -g rg-legalmind-<env> -n ca-legalmind-api-<env> \
  --query "[].{name:name, created:properties.createdTime, image:properties.template.containers[0].image}" -o table
az containerapp update -g rg-legalmind-<env> -n ca-legalmind-api-<env> --image <previous image>
```

Then revert the commit on `main`, so the next CD run doesn't bring it back.

---

## API 5xx rate (`api-5xx-rate`, severity 1)

**Means:** more than 2% of API requests failed with a server error in 5 minutes. Users see "Er is een fout opgetreden".

**First query:**

```kusto
requests
| where timestamp > ago(30m) and toint(resultCode) >= 500
| summarize failures = count() by name, resultCode, bin(timestamp, 5m)
```

Then the exception behind one failed request:

```kusto
exceptions | where timestamp > ago(30m) | project timestamp, type, outerMessage, operation_Name | take 20
```

The exception is often just `HttpResponseError`; the service's answer (`Forbidden`, `ResourceNotFound`, ...) is in the container's log:

```bash
az containerapp logs show -g rg-legalmind-<env> -n ca-legalmind-api-<env> --tail 100 --type console | grep -iE "forbidden|error"
```

**Likely causes and fixes:**

| You see | Cause | Fix |
|---|---|---|
| `Forbidden` / `AuthorizationFailed` from Search, Cosmos DB, Storage or OpenAI | The app's managed identity lost a role (someone removed it, or a resource was recreated) | Re-apply the infrastructure, which recreates every role assignment: `gh workflow run cd.yml -f environment=<env> -f reapply_infra=true` (or `./scripts/deploy.sh -e <env> --skip-build --tag <running tag> --reapply-infra`). A plain redeploy is **not** enough: it skips the Bicep when `infra/` is unchanged in git |
| `ClientAuthenticationError`, `ManagedIdentityCredential` | The identity isn't attached, or `AZURE_CLIENT_ID` is wrong | Same re-apply |
| `RateLimitError` / 429 | Model throttling: see OpenAI throttling | |
| `Free Query Semantic Usage exceeded for the month` | The semantic ranker's free plan (1,000 queries a month) is used up. Since 21 Nov 2026 search falls back to plain hybrid search for an hour at a time (log event `semantic_ranking_unavailable`), so this should only show as slightly weaker ranking, not errors | Nothing urgent; it resets on the 1st. To keep semantic ranking, switch the service to the standard plan (`semanticSearch` in `infra/modules/ai-search.bicep`, about €1 per 1,000 queries after the free ones) |
| One route only, right after a deploy | A bug in the new revision | Roll back |

---

## Chat latency (`chat-latency-p95`, severity 2)

**Means:** the slowest 5% of answers took more than 20 seconds over 15 minutes.

**First query:** which stage is slow?

```kusto
dependencies
| where timestamp > ago(1h) and name startswith "legalmind."
| summarize p50 = percentile(duration, 50), p95 = percentile(duration, 95), calls = count() by name
| order by p95 desc
```

**Likely causes:** `legalmind.agent` slow → the model is slow or throttled (check the 429 alert and the OpenAI account's latency metric); `legalmind.retrieval` slow → AI Search under load (Basic tier, one replica); `legalmind.guardrails.*` slow → Content Safety or Language. A cold start after scale-to-zero adds 10–20 s to the first request in dev.

**Fix:** throttling → see below; Search → add a replica; cold starts in prod → `minReplicas` is already 2 there.

---

## OpenAI throttling (`openai-429`, severity 2)

**Means:** the primary Azure OpenAI account answered 429: a deployment's tokens-per-minute quota is used up.

- **Through the AI gateway (dev):** the gateway's circuit breaker takes the primary out of the pool for a minute (or for its `Retry-After`) and sends calls to the second region (`oai-…-b`). Users should see nothing; the alert says the primary is saturated, not that users are affected. Check the second region isn't throttled too (second query).
- **Without the gateway:** the SDK retries, so users first see slower answers, then errors.
- **A 429 from the gateway itself** (header `x-legalmind-user-tokens-remaining: 0`) is one user over their per-user token limit (`userTokensPerMinute`), not the model. That one doesn't fire this alert.

**First query:** who is using the tokens?

```kusto
customMetrics
| where timestamp > ago(1h) and name == "Total Tokens"
| summarize tokens = sum(valueSum) by user = tostring(customDimensions["User"]), deployment = tostring(customDimensions["Deployment"])
| top 10 by tokens
```

Without the gateway, the app's own spans: `dependencies | where isnotnull(customDimensions["gen_ai.usage.input_tokens"])`.

**Second query:** is the gateway failing over, and is the second region keeping up?

```kusto
dependencies
| where timestamp > ago(1h) and cloud_RoleName has "apim"
| summarize calls = count() by backend = target, resultCode, bin(timestamp, 5m)
```

**Likely causes:** a traffic spike, a load test, or a lowered deployment capacity. Evaluations use their own deployment (`gpt-4.1-mini-eval`) since L6, so they no longer throttle the app.

**Fix:** check the deployment's capacity (`az cognitiveservices account deployment show -g rg-legalmind-<env> -n <openai account> --deployment-name gpt-4.1-mini --query sku`) against `chatCapacity` in `infra/parameters/<env>.bicepparam`; `deploy.sh --reapply-infra` restores it. If the traffic is real, raise the capacity there (Global Standard capacity costs nothing until used). A capacity change takes effect only after a few minutes (about 6 on 21 Nov 2026), so after raising it, wait before you judge the result. If both regions throttle, one user may be using it all: lower `userTokensPerMinute`.

---

## Unverified answers (`answers-unverified`, severity 2)

**Means:** more than 20% of answers in an hour couldn't be checked against the documents. The groundedness check failed (it fails closed and labels the answer `unverified`). Answers still go out, with a warning label, so this is a quality alert rather than an outage.

**First query:**

```kusto
requests
| where timestamp > ago(2h) and name has "/api/chat"
| extend grounding = tostring(customDimensions["legalmind.grounding"])
| summarize answers = count() by grounding, bin(timestamp, 15m)
```

then why the check failed:

```kusto
traces | where timestamp > ago(2h) and message has "groundedness" and severityLevel >= 2 | take 20
```

**Likely causes:** the guardrail's model call fails (throttling, a deployment renamed); invalid JSON from the model after a prompt change (the evaluation gate's "Prompts changed" note on the PR would show it).

**Fix:** throttling → see above; prompt change → roll back.

---

## Container restarts (`api-restarts`, severity 1)

**Means:** the API container restarted: it crashed, ran out of memory, or failed its health probe.

**First query** (Log Analytics `log-legalmind-<env>`):

```kusto
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(1h) and ContainerAppName_s == "ca-legalmind-api-<env>"
| project TimeGenerated, Reason_s, Log_s
| order by TimeGenerated desc
```

and the app's last words before it stopped:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1h) and ContainerAppName_s == "ca-legalmind-api-<env>"
| project TimeGenerated, Log_s | order by TimeGenerated desc | take 50
```

To test this alert: a revision restart doesn't count (it creates new replicas), and SIGKILL to PID 1 from inside the container is ignored; stop the process with SIGTERM:
`az containerapp exec -g rg-legalmind-<env> -n ca-legalmind-api-<env> --command "python -c __import__('os').kill(1,15)"`.

**Likely causes:** `OOMKilled` → raise memory in `container-apps.bicep`; probe failures right after start → startup is slow (index creation, cold dependencies); a crash loop after a deploy → roll back.

---

## After an incident

Write a short postmortem in `docs/postmortems/` (timeline, cause, fix, prevention), and add what you learned to this runbook. Example: [the incident drills of 20 November 2026](postmortems/2026-11-20-incident-drills.md), which tested every alert and corrected this runbook twice.
