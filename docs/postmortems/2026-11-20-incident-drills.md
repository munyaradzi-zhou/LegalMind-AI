# Postmortem: incident drills on dev, 20 November 2026

Planned drills to check that every alert in `infra/modules/alerts.bicep` fires and that `docs/runbook.md` gets an on-call engineer to the fix. Environment: dev. All times UTC.

## Summary

| Drill | Broken on purpose | Alert | Fired after | Outcome |
|---|---|---|---|---|
| 1 | Chat model quota cut from 30K to 1K tokens/min | OpenAI throttling | (already firing, see finding 3) | 129 throttled calls, answers took 50–78 s or timed out |
| 1 | (same) | Chat latency p95 | 0 min (latency already high from the throttling) | |
| 1 | (same) | Unverified answers | 3 min after enough answers were labelled | 8 of 8 answers labelled `unverified`: the guardrail's model call was throttled too |
| 3 | API process stopped inside the container | API container restarts | 3.5 min | Container restarted by the platform |
| 2 | App identity's Search data role removed | API 5xx rate | 7 min after the first error | Every search and chat failed with 500 for 22 minutes |

All five alerts fired and emailed the action group. Four drills went as expected. Drill 2 found that the runbook's fix didn't work.

## Timeline

| Time | Event |
|---|---|
| 21:28 | OpenAI throttling alert fires, before any drill: real throttling (finding 3) |
| 21:31 | Drill 1: model capacity set to 1K TPM, 10 chats sent. Chat latency alert fires at 21:31 |
| 21:35 | Drill 1 shows no grounding labels in Application Insights (finding 1); fix deployed |
| 21:40 | Drill 1, round 2: 12 chats; 3 answered in 66–78 s, 9 timed out on the client after 200 s |
| 21:46 | Capacity restored to 30K TPM |
| 21:49 | Unverified answers alert fires |
| 21:47–22:15 | Drill 3: a revision restart and two SIGKILLs don't count as a container restart (finding 4); SIGTERM does |
| 22:19 | Container restart alert fires |
| 22:22 | Drill 2: Search Index Data Contributor removed from `id-legalmind-dev` |
| 22:23 | First 500 from `/api/search` |
| 22:30 | API 5xx rate alert fires |
| 22:31–22:33 | Runbook "API 5xx rate": first query shows `/api/search` failing; the exceptions only say `HttpResponseError`; the container logs show `Forbidden` from AI Search. Runbook row: the identity lost a role, so redeploy the infrastructure |
| 22:37 | Redeploy finished: smoke tests still fail, role still missing (finding 2) |
| 22:39 | Cause found: `deploy.sh` only applies Bicep when `infra/` changed in git. Added `--reapply-infra` |
| 22:45 | Redeploy with `reapply_infra=true`: role restored, smoke tests pass. **Resolved, 22 minutes after the first error** |

## Findings and fixes

1. **The quality alert could never have fired.** The grounding label was set on FastAPI's internal `fastapi.endpoint` span, which doesn't reach the `requests` table. *Fixed:* the observability middleware remembers the request's own span, and a unit test covers it.
2. **The runbook's fix for a lost role didn't work.** CD and `deploy.sh` skip the Bicep template when `infra/` hasn't changed in git, so drift (a deleted role assignment, a changed setting) is never repaired. *Fixed:* `deploy.sh --reapply-infra` and the CD workflow's `reapply_infra` input; the runbook now uses them.
3. **Real throttling on dev.** The OpenAI throttling alert fired three minutes before the drill. The evaluation gate on PR #4 was running at the time against dev's 30K TPM deployment. Drill 1 then throttled the model further while that evaluation was still running, and the evaluation crashed on a 429 at 21:32:59 (the gate reported a failure that had nothing to do with the PR; it was re-run on main). *Follow-up:* give evaluations their own deployment, or more capacity, so they can't throttle the app; before a drill, check that no evaluation or deployment is using the environment.
4. **Restarts are hard to trigger on purpose.** A revision restart creates new replicas, not a container restart. SIGKILL to PID 1 from inside the container is ignored by the kernel. SIGTERM works. *Documented* in the runbook.
5. **The deploy smoke test was flaky.** Its 30 s timeout failed the last three CD runs after successful deploys, because a chat answer takes 15–35 s. *Fixed:* 120 s.
6. **Error messages hide the cause.** The 5xx alert's exceptions said only `HttpResponseError`; the `Forbidden` was in the container logs. *Fixed in the runbook:* its first queries now include the console logs.

## What went well

- Every alert fired within minutes and arrived by email.
- The workbook and the stage spans showed at once which stage was slow (`legalmind.agent` while throttled).
- Users were warned rather than misled during drill 1: answers were labelled `unverified`, not silently passed as grounded.

## Prevention

- Run these drills again after changes to the alerts or the deployment, and only when no evaluation run or deployment is using the environment (`gh run list --status in_progress`).
- Follow-up (L6): a separate model deployment, or more capacity, for evaluations.
