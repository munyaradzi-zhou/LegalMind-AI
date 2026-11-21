# What LegalMind costs

Measured on the dev environment (Cost Management, 12–21 November 2026, EUR) and from the
model calls of real answers (Application Insights). List prices are Azure's pay-as-you-go
prices at the time; your region and agreement may differ.

## Dev, as it runs

| Resource | Cost | Notes |
|---|---|---|
| **Azure AI Search, Basic** | **about €2.05 a day, €62 a month** | Fixed: billed per hour whether used or not. Two thirds of everything |
| Azure OpenAI (gpt-4.1-mini, embeddings) | €7.40 in 10 days, mostly on busy days | Per token. Evaluation runs and load tests are the big days |
| API Management, Developer (the AI gateway) | about €1.50 a day, €44 a month (list price) | Since 21 Nov. Consumption, before it, cost nothing at this traffic |
| Container Registry, Basic | €0.13 a day, €4 a month | |
| "MS Bing Services" meter, billed on the Search service | €1.22 in 10 days | Shown by Cost Management under AI Search |
| Content Safety, Language, Document Intelligence | under €1 in 10 days | Per call |
| Container Apps (API, web, MCP) | no charge shown | Scale to zero when idle |
| Cosmos DB serverless, Storage, Key Vault, Log Analytics, Application Insights | cents | Per use |

**Total at rest: €2.33 a day (about €70 a month) before the gateway, about €3.80 a day
(€115 a month) with it.** Busy days: €8.29 (corpus ingestion and evaluations, 14 Nov),
€4.57 (incident drills, 20 Nov).

## Cost per answer

Measured over 657 answers (14–21 Nov), from the token counts each model call reports:

| | Per answer |
|---|---|
| Model calls | 3.3 (routing, the agent and its tools, the groundedness check) |
| Input tokens | 10,100 (the 8 retrieved passages are most of it) |
| Output tokens | 490 |
| gpt-4.1-mini (USD 0.40 / 1.60 per million tokens in / out) | **about €0.0045** |
| Content Safety and PII checks (question and answer) | about €0.003 |
| Embedding the question, semantic ranker (standard plan) | about €0.001 |
| **Total, variable** | **about €0.0085, under 1 cent** |

Fixed costs dominate: at 1,000 answers a day the variable part is about €8 a day, still
less than AI Search, the gateway and the rest at rest; at 100 a day it's €0.80.

## What the levers are worth

| Lever | Effect | Measured |
|---|---|---|
| Semantic cache at the gateway (L6) | Saves the calls it answers from cache | **18% of model calls on exact repeats, no faster answers, and 7% of first-time calls answered from similar but different prompts.** Redis costs €1–2 a day: at this traffic it costs more than it saves. Kept off (`docs/load-test-2026-11-21.md`) |
| Query rewriting (ADR 0004) | +1 model call and +1.3 s per question | Off; would add about €0.0005 per answer |
| Prompt caching (automatic in Azure OpenAI) | Cheaper repeated prompt prefixes | 31% of input tokens cached on the first day (12 Nov dashboards), from the long, stable system prompts |
| Network isolation (ADR 0008) | +9 private endpoints | About €60 a month (list price); off in dev |
| Second region for failover (L6) | A second Azure OpenAI account | No fixed cost: Global Standard capacity is free until used |
| Scale to zero (Container Apps) | No cost when idle | Confirmed on the dashboards (0 → 1 → 2 → 0 replicas); the first question after idle waits for a cold start |

## Where the money would go next

- **AI Search** is the fixed cost to watch. Basic is the smallest tier with the semantic
  ranker; Standard (about €225 a month) only when the index outgrows Basic's 15 GB or more
  replicas are needed for availability.
- **The semantic ranker's free plan** (1,000 queries a month) ran out in three weeks of
  evaluations; production would use the standard plan (about €1 per 1,000 queries after the
  free ones), roughly €0.001 per answer.
- **Production API Management** with an SLA and VNet integration (Standard v2, about
  €640 a month list) would be the largest line in a production setup; Basic v2 (about
  €140 a month) if the gateway doesn't need to reach private endpoints.
- **Model capacity** costs nothing until used: the load test's ceiling (about 30 answers a
  minute) is raised by asking for more capacity, not by paying more per answer.
