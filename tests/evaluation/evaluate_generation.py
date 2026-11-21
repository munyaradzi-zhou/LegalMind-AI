"""End-to-end answer quality through the live API (datasets/generation_golden_set.json).

For every question the script calls POST /api/chat (a fresh session each), then scores:

- fact recall:     share of expected facts present in the answer (any listed variant counts)
- right source:    a citation comes from the expected document
- groundedness:    an LLM judge reads the passages the answer cites and rates 1-5 how well
                   every claim is supported by them, listing unsupported claims (the corpus is
                   too large to give the judge every document, and fact recall already checks
                   correctness against the golden facts)
- refusal:         for unanswerable questions, whether the answer says the documents don't say
- safe:            for unanswerable questions, declined *or* labelled by the app's own groundedness
                   check as not backed by the documents (the user is warned either way). Some are
                   near misses: the corpus has the fact for a different policy or person.

The judge is the same model family as the generator (gpt-4.1-mini), which tends to be lenient;
treat its scores as a signal, not ground truth.

    API_URL=https://<api> python -m tests.evaluation.evaluate_generation
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from collections import Counter
from typing import Any

import httpx
import truststore

from legalmind.services.azure_openai import AzureOpenAIService
from tests.evaluation._common import (
    load_dataset,
    markdown_table,
    mean,
    normalise,
    percentile,
    write_report,
)

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")

JUDGE_PROMPT = """You are a strict evaluator of a legal assistant for a Dutch legal expenses insurer.
You get SOURCE DOCUMENTS (the only knowledge the assistant was supposed to use), a QUESTION and an ANSWER.

Return JSON only, with these keys:
- "groundedness": integer 1-5. 5 = every factual claim is supported by the source documents;
  3 = mostly supported, some unsupported additions; 1 = mainly unsupported or contradicting the sources.
  General disclaimers and advice to consult a lawyer are not factual claims.
- "unsupported_claims": list of short strings, each a claim not supported by the sources (empty if none).
- "answers_question": true if the answer addresses what was asked.
- "says_not_in_documents": true if the answer states that the documents do not contain the requested information.
  Also true when it says the requested item isn't there and offers a related item that it clearly
  labels as something else (e.g. "the only policy number is that of a different insurance").
"""


def _cited_passages(reply: dict[str, Any]) -> str:
    """The passages the answer cites, as the judge's source documents."""
    parts = [
        f"=== [{c['index']}] {c['document_title']} (p. {c.get('page_number')}) ===\n{c.get('chunk_content', '')}"
        for c in reply.get("citations", [])
    ]
    return "\n\n".join(parts) or "(the answer cites no passages)"


def _fact_recall(answer: str, facts: list[list[str]]) -> tuple[float, list[str]]:
    text = normalise(answer)
    missing = [" / ".join(variants) for variants in facts if not any(normalise(v) in text for v in variants)]
    recall = 1.0 - len(missing) / len(facts) if facts else 1.0
    return recall, missing


async def _judge(openai: AzureOpenAIService, sources: str, question: str, answer: str) -> dict[str, Any]:
    response = await openai.chat_completion(
        [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"SOURCE DOCUMENTS:\n{sources}\n\nQUESTION:\n{question}\n\nANSWER:\n{answer}"},
        ],
        temperature=0.0,
        max_tokens=600,
    )
    content = response["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    try:
        verdict: dict[str, Any] = json.loads(content[start : end + 1])
    except ValueError:
        verdict = {"groundedness": 0, "unsupported_claims": ["judge returned invalid JSON"]}
    return verdict


async def evaluate_generation() -> dict[str, Any]:
    queries = load_dataset("generation_golden_set.json")
    openai = AzureOpenAIService()
    details: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=240, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as client:
        health = (await client.get(f"{API_URL}/health")).json()
        for q in queries:
            started = time.perf_counter()
            response = await client.post(
                f"{API_URL}/api/chat", json={"message": q["question"], "user_id": "eval-generation", "stream": False}
            )
            latency = time.perf_counter() - started
            response.raise_for_status()
            reply = response.json()
            answer = reply["message"]
            recall, missing = _fact_recall(answer, q["must_contain"])
            verdict = await _judge(openai, _cited_passages(reply), q["question"], answer)
            cited = {c["document_title"] for c in reply.get("citations", [])}
            details.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "answerable": q["answerable"],
                    "agent": reply.get("agent_name", ""),
                    "latency_s": round(latency, 2),
                    "fact_recall": recall,
                    "missing_facts": missing,
                    "right_source": (q["source"] in cited) if q["answerable"] else None,
                    "citations": len(reply.get("citations", [])),
                    "groundedness": verdict.get("groundedness", 0),
                    "unsupported_claims": verdict.get("unsupported_claims", []),
                    "says_not_in_documents": bool(verdict.get("says_not_in_documents")),
                    "app_grounding": reply.get("grounding", ""),
                    "answer": answer,
                }
            )

    answerable = [d for d in details if d["answerable"]]
    unanswerable = [d for d in details if not d["answerable"]]
    latencies = [d["latency_s"] for d in details]
    summary = {
        "questions": len(details),
        "prompts": health.get("prompts", ""),
        "fact_recall": mean([d["fact_recall"] for d in answerable]),
        "all_facts_present": mean([1.0 if d["fact_recall"] == 1.0 else 0.0 for d in answerable]),
        "right_source_cited": mean([1.0 if d["right_source"] else 0.0 for d in answerable]),
        "groundedness_mean": mean([float(d["groundedness"]) for d in answerable]),
        "grounded_4plus": mean([1.0 if d["groundedness"] >= 4 else 0.0 for d in answerable]),
        "refusal_rate_unanswerable": mean([1.0 if d["says_not_in_documents"] else 0.0 for d in unanswerable]),
        # Unanswerable questions that the user is protected from: declined, or answered but
        # labelled by the app's own groundedness check as not backed by the documents.
        "unanswerable_safe": mean(
            [1.0 if d["says_not_in_documents"] or d["app_grounding"] != "documents" else 0.0 for d in unanswerable]
        ),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "agents": dict(Counter(d["agent"] for d in details)),
    }

    per_question = [
        {
            "id": d["id"],
            "agent": d["agent"],
            "facts": f"{d['fact_recall']:.0%}",
            "source": {True: "yes", False: "no", None: "-"}[d["right_source"]],
            "grounded": d["groundedness"],
            "refused": "yes" if d["says_not_in_documents"] else "",
            "app label": d["app_grounding"],
            "latency_s": d["latency_s"],
            "missing / unsupported": "; ".join(d["missing_facts"] + d["unsupported_claims"])[:160],
        }
        for d in details
    ]
    md = (
        "# Generation evaluation (end to end, live API)\n\n"
        f"API: `{API_URL}` (prompts `{summary['prompts']}`) — {len(answerable)} answerable and "
        f"{len(unanswerable)} unanswerable questions.\n\n"
        "## Summary\n\n"
        + markdown_table([{k: v for k, v in summary.items() if k not in ("agents", "prompts")}])
        + f"\nAgents used: {summary['agents']}\n\n"
        "## Per question\n\n" + markdown_table(per_question)
    )
    write_report("generation", summary, details, md)
    return summary


if __name__ == "__main__":
    asyncio.run(evaluate_generation())
