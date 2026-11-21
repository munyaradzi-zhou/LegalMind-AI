# ADR 0005: A groundedness check on every answer, which fails closed and labels

- Status: accepted
- Date: 2026-11-21 (decided 12 November, after the dashboards showed the check failing open)

## Context

A legal answer with a citation list *looks* sourced whether it is or not. On the first
Azure run (12 Nov) the research agent answered a notice-period question from model memory,
citing articles of the BW, with eight "sources" listed underneath: none of them supported
it. Users (case handlers, lawyers) need to know, for every answer, whether the cited
sources actually back it.

## Decision

After the agent answers, a **groundedness check** (one gpt-4.1-mini call, JSON mode) reads
the question, the answer and the sources it may rest on (the retrieved passages and, since
L7, the live legislation and rulings, live sources first, up to 24,000 characters) and
returns a score, the unsupported claims and the sources the answer uses. Every answer gets
one of four **labels**, shown in the UI and returned over MCP:

| Label | Meaning |
|---|---|
| `documents` | checked: supported by the sources |
| `not_grounded` | checked: contains claims the sources don't support |
| `unverified` | **the check itself failed** (error, unparseable output): the answer is not presented as checked |
| `no_documents` | nothing relevant was retrieved; general knowledge only |

The check **fails closed**: any error yields `unverified`, never "assume it's grounded".
Answers are **labelled, not blocked**: a partly supported answer is still useful to a
professional, if it says so.

## Evidence

- **Failing open, found in production:** on 12 Nov the dashboards showed 52
  `JSONDecodeError`s in the check; the code had logged "assuming grounded" each time, so
  the guard silently did nothing. After JSON mode, tolerant parsing and failing closed:
  0 errors in the next run.
- **What it catches, and what it can't:** "right number, wrong document" (a premium of
  one policy given for another) passed a claim-level check, because the number *is* in
  the sources. Three iterations of the check caught 1 of 3 such near-misses; the fix that
  worked was in the data (contextual chunk headers, ADR 0006), after which the agent
  declined 8 of 8.
- The `answers-unverified` alert (L5) fires when more than 20% of answers in an hour are
  `unverified`; it fired in the drill that throttled the model (8 of 8 answers labelled
  `unverified`, rather than passed off as checked).
- The 8,000-character context limit hid live sources (L7, 21 Nov): the first live-law
  answer was labelled `not_grounded`. Fixed by putting live sources first and raising the
  limit to 24,000; then 8 of 8 live-source answers were labelled `documents`.

## Alternatives considered

| Option | Why not |
|---|---|
| No check (trust citations) | The 12 Nov example: citations don't prove support |
| Azure AI Content Safety groundedness detection (preview) | Preview, limited regions and languages (Dutch), and no control over what is checked (question-aware, which sources the answer uses) |
| Block ungrounded answers | Hides partly useful answers from professionals; the label carries the same warning |
| A reflection loop (agent revises until grounded) | More calls and latency per answer; the check plus the label is enough to warn |

## Consequences

- One extra model call per answer (about 1–2 s); it runs on every answer.
- The judge can only check what it is given: if retrieval misses the right passage, a
  correct answer from general knowledge is labelled `not_grounded`.
- The same model family checks its own family's output; the evaluation gate measures the
  labels against human-written expected facts to keep the check honest.
- The UI and MCP clients must show the label; the web front end (L8) shows it on every answer.
