# ADR 0006: Contextual chunk headers instead of a stricter answer check

- Status: accepted
- Date: 2026-11-21 (decided 12 November)

## Context

The evaluation on 12 November found answers that were **right number, wrong document**:
"Hoeveel bedraagt de premie van de reisverzekering?" (there is no travel policy) was
answered "€180 per jaar [1]". €180 is the premium of the *legal-expenses* policy. The
retrieved chunk read "ARTIKEL 6 - PREMIE - De jaarpremie bedraagt €180,00": nothing in it
said which policy it belonged to. Neither the agent nor the groundedness check could tell.

The first response was to make the groundedness check stricter (ADR 0005). Three iterations
caught at most 1 of 3 such near-misses and, in one version, raised false alarms on careful
refusals.

## Decision

At ingestion every chunk starts with a **contextual header**: one sentence that identifies
its document (`Document: <one sentence identifying the document>`), written
by one gpt-4.1-mini call per document and prepended to the chunk's **content** before it
is embedded and indexed. Documents also get meaningful titles. The header prompt forbids
inferring authorship (a first version attributed a case summary to an insurer that was
merely mentioned in it).

## Evidence

Golden set of 27 questions, before and after re-seeding with headers (12 Nov):

| | Stricter check (fix 3) | **Headers** |
|---|---|---|
| Expected facts found | 92% | **97%** |
| All facts present | 89% | **95%** |
| Groundedness (judge, 1–5) | 4.74 | **4.95** |
| Unanswerable questions declined | 6/8 (by hand) | **8/8** |

- The travel-insurance question now declines *and* attributes the fact correctly: "no
  information about a travel policy; the documents concern a legal-expenses policy with a
  premium of €180 [1]".
- "Volgende stappen in de zaak De Vries": 0% → 100% of facts, because the header names
  the case, so the case file's next-steps section is retrieved instead of the policy's
  general claims procedure.
- Retrieval at passage level was mixed: hit@1 for vector and hybrid went up (60–63% → 71%),
  hybrid + semantic hit@3 went down (94% → 83%): chunks of one document share a header and
  look more alike.

## Alternatives considered

| Option | Why not |
|---|---|
| Stricter groundedness check | Tried three times: caught 1 of 3, added false alarms. The problem was in the data, not the check |
| A separate metadata field (title, document type) | Needs a schema and semantic configuration change, and only helps the parts that read that field; text in the content is seen by BM25, the vectors, the ranker, the agent and the judge alike |
| Better titles only | Titles aren't in the chunk text the agent and judge read |

## Consequences

- One extra model call per document at ingestion (not per question).
- Headers are model output: they can be wrong and need the same checking as any answer.
- Changing the header prompt means re-indexing (`replace_existing` keeps the index free of
  duplicates).
- Within-document chunks are more alike, which costs a little rank-3 precision; the gain in
  answers outweighs it.
