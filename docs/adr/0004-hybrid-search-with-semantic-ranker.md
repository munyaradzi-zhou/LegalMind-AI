# ADR 0004: Hybrid search with the semantic ranker; query rewriting off

- Status: accepted
- Date: 2026-11-21 (decided 12 November from the first measurements, re-measured since)

## Context

Legal questions mix paraphrase ("can my landlord end the lease?") with exact identifiers
("artikel 7:271 BW", "ECLI:NL:HR:2020:1946", a policy number). The documents are Dutch;
some questions are English. Every retrieval layer costs latency, and some cost money.

## Decision

Retrieval is **hybrid** (BM25 keywords and HNSW vectors, merged by Reciprocal Rank Fusion
in Azure AI Search) **re-ranked by the semantic ranker**, top 8 passages, with the caller's
security filter (ADR 0001). **LLM query rewriting is off by default**
(`QUERY_REWRITING_ENABLED`). Since 21 November search falls back to plain hybrid when the
semantic ranker's quota is used up, instead of failing.

## Evidence

`tests/evaluation/evaluate_retrieval.py` against the golden set (68 questions with the
expected passage, 23 documents, 20 Nov):

| Mode | hit@1 | hit@3 | hit@5 | MRR@10 | p50 |
|---|---|---|---|---|---|
| Keyword (BM25) | 0.40 | 0.56 | 0.62 | 0.50 | 2.6 s |
| Vector | 0.65 | 0.82 | 0.91 | 0.76 | 3.5 s |
| Hybrid | 0.60 | 0.74 | 0.87 | 0.71 | 3.2 s |
| **Hybrid + semantic ranker** | **0.66** | **0.88** | **0.93** | **0.77** | 3.3 s |
| Hybrid + semantic + LLM rewriting | 0.75 | 0.90 | 0.96 | 0.83 | 4.7 s |

- **The semantic ranker is the largest single gain.** Measured twice: +17 points hit@1 on
  the first corpus (12 Nov: 60% → 77%), and, when its free quota ran out and search fell
  back on 21 Nov, the evaluation gate saw top-3 drop from 0.88 to 0.75 and MRR from 0.77
  to 0.71.
- Keywords alone miss paraphrases; vectors alone miss exact references now and then; the
  combination is steadier than either.
- **Query rewriting:** on the first corpus (3 documents) it added 1.3 s for no gain, so it
  was switched off. On the larger corpus it helps: hit@1 0.66 → 0.75, and English questions
  over Dutch documents hit@3 0.70 → 1.00. The cost is still 1.3 s and one more model call
  per question.

## Decision on rewriting, revisited

It stays **off by default**, as a setting: the gain is mostly at rank 1, the agent reads all
8 passages anyway, answers were already 96% complete (fact recall, evaluation gate), and the
extra second matters in a chat. For a deployment with many English-speaking users it should
be turned on, and the evaluation gate will show the effect.

## Alternatives considered

| Option | Why not |
|---|---|
| Vector only | Simplest and close at rank 1 (0.65 vs 0.66), but lower at rank 3 (0.82 vs 0.88) and in MRR (0.76 vs 0.77), and keyword matching on exact identifiers is lost |
| AI Search integrated vectorisation / indexers | Less code, but chunking, contextual headers (ADR 0006) and access fields are ours to control |
| A cross-encoder ranker of our own | Hosting and latency for a gain the semantic ranker already gives |

## Consequences

- AI Search Basic is the largest fixed cost (about €70 a month, see `docs/costs.md`). The
  semantic ranker's free plan covers 1,000 queries a month, which evaluation runs used up in
  three weeks; the standard plan costs about €1 per 1,000 queries after that.
- Search keeps working without the ranker, with weaker ranking (log event
  `semantic_ranking_unavailable`; runbook "API 5xx rate").
