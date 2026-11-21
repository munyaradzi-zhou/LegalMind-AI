# Architecture decision records

Why LegalMind is built the way it is. Each record gives the context, the decision, the
alternatives that were considered and, where possible, the measurement that decided it.

| # | Decision | Deciding evidence |
|---|---|---|
| [0001](0001-document-level-security-trimming.md) | Document-level security trimming with a group filter field, enforced in the search engine | 340 searches by 5 test users: 0 leaks; 41 outsider questions: 0 forbidden citations |
| [0002](0002-ai-gateway-in-api-management.md) | API Management as the AI gateway: per-user token limits, failover between regions | Primary throttled: 160 calls failed over, 0 errors for users; semantic cache measured and kept off |
| [0003](0003-semantic-kernel-for-the-specialist-agents.md) | Semantic Kernel for four specialist agents behind a router | Routing measured on its own: 92% → 100% after one prompt fix |
| [0004](0004-hybrid-search-with-semantic-ranker.md) | Hybrid search with the semantic ranker; query rewriting off | Ranker: +17 points hit@1; rewriting: +1.3 s for a gain mostly at rank 1 |
| [0005](0005-groundedness-check-that-fails-closed.md) | A groundedness check on every answer that fails closed and labels | It had failed open 52 times unnoticed; 0 since |
| [0006](0006-contextual-chunk-headers.md) | Contextual chunk headers instead of a stricter answer check | Near-miss unanswerables declined 6/8 → 8/8; facts 92% → 97% |
| [0007](0007-managed-identity-and-no-keys.md) | One user-assigned managed identity and no keys | Key authentication off on every service; drills show what a lost role does |
| [0008](0008-network-isolation-as-a-parameter.md) | Network isolation as a deployment parameter | Isolated: 9/9 services refused from the internet; answers still work inside the VNet |

Costs, and what each lever is worth: [`../costs.md`](../costs.md). Load test and failover:
[`../load-test-2026-11-21.md`](../load-test-2026-11-21.md).
