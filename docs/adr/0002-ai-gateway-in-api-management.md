# ADR 0002: API Management as the AI gateway for the app's model calls

- Status: accepted
- Date: 2026-11-21

## Context

Every answer makes several model calls: routing, query rewriting, the agent itself, and
the groundedness check. Until now the API called one Azure OpenAI deployment directly:

- one deployment's tokens-per-minute quota was the whole system's ceiling. The incident
  drills (docs/postmortems/2026-11-20-incident-drills.md) showed what happens when it runs
  out: an evaluation run throttled the app, and throttled answers took 50–78 s or timed out.
- nothing stopped one user (or one runaway agent loop) from using all of it.
- token use was visible per request in Application Insights, but not per user.

API Management was already deployed in front of the API, on the Consumption tier.

## Decision

Put API Management between the app and Azure OpenAI as an AI gateway (`infra/modules/api-management.bicep`):

- **Who may call:** `validate-azure-ad-token` accepts only the app's managed identity
  (audience `https://cognitiveservices.azure.com`, so the SDK code is unchanged apart
  from the endpoint). The gateway calls the models with its own managed identity.
- **Per-user token limit:** `llm-token-limit` keyed on `x-legalmind-user`, which the app
  sets from the signed-in user (L3). Only the app can call the gateway, so the header is trusted.
- **Token metrics per user and deployment:** `llm-emit-token-metric` into Application
  Insights (the operations workbook shows them).
- **Failover:** a backend pool with the primary account (priority 1) and a second account
  in another region (priority 2, France Central for dev), with the same deployment names.
  Each backend has a circuit breaker that trips on one 429 or 5xx (and honours
  `Retry-After`); a `retry` in the backend section sends the call to the next backend.
- **Semantic cache (optional, measured):** `llm-semantic-cache-lookup` and `-store` on
  chat completions, with Azure Managed Redis. The cache is partitioned (`vary-by`) on
  `x-legalmind-access`, a hash of the user's search filter: a cached answer is built from
  passages, and with security trimming two users asking the same question may be allowed
  to see different passages. Users with the same access share entries; nobody gets an
  answer drawn from documents they can't see.
- **Evaluations get their own deployment** (`gpt-4.1-mini-eval`) and call the account
  directly, so a quality gate can't throttle the app again.

**Tier: Developer for dev.** Consumption supports neither `llm-token-limit` nor backend
circuit breakers, and can't be upgraded in place, so the gateway is a new instance
(`apim-<project>-<env>-<suffix>-<tier>`). Developer (about €1.5 a day, no SLA, slow to
provision) has every policy the gateway needs. Production would use Standard v2 or Premium
(SLA, autoscale, and VNet integration to reach private endpoints); `apimSku` is a parameter.
With `networkIsolation` the gateway is off, because this gateway isn't in the VNet.

## Consequences

- One more hop on every model call (a few milliseconds) and one more service to run.
- A model call now fails with 401 if anything other than the app's identity calls the
  gateway. Local development and the evaluation gate call the account directly
  (`scripts/azure-env.sh`, `.github/workflows/eval.yml`).
- The per-user limit and the cache only work if the app sends the headers:
  `services/model_gateway.py` adds them to every call from both SDK clients (the app's
  and Semantic Kernel's) when `AZURE_OPENAI_VIA_GATEWAY=true`.
- Measured on 21 Nov 2026 (`docs/load-test-2026-11-21.md`): the gateway moved traffic to the
  second region when the primary was throttled, with no errors for users; throughput is
  bounded by model capacity, not the gateway. The semantic cache answered only 18% of model
  calls even on exact repeats, gave no speed gain and reused calls across different questions,
  so it stays off.
- The `openai-429` alert now means "the primary is saturated", not "users are affected";
  the runbook says how to tell the two apart.
