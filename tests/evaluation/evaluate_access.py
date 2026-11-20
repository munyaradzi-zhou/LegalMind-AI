"""Document-level security trimming on the real index and the live API: does anything leak?

Four test users (the X-Dev-* headers; the API must run with sign-in off):

    anna   Lawyer     matter BM/2026/H-0412 (tenancy, De Wit)
    bram   Lawyer     matter BM/2026/A-0377 (employment, Kramer)
    vera   Lawyer     matter VRB/2024/ZK-9012 (De Vries, whose case summary is privileged)
    piet   Paralegal  matter VRB/2024/ZK-9012 (so not the privileged case summary)
    guest  no role    no matters: public law only

1. Retrieval: every retrieval golden question as every user (production search, top 10).
   A *leak* is a returned chunk from a matter the user isn't on, or a privileged chunk for
   someone without the Lawyer role. Also: hit@5 on the questions each user may see, against
   unrestricted search, to check that trimming doesn't cost the allowed users anything.
2. Answers: every answerable case-file question from the generation golden set, asked through
   /api/chat by a user who may NOT see that file. It must cite nothing from the file and
   must not give its facts. Then a few questions asked by both the file's user and an
   outsider, side by side: the same question, two correctly trimmed answers.

Leaks must be 0; there is no tolerance.

    python -m tests.evaluation.evaluate_access                 # both parts
    python -m tests.evaluation.evaluate_access --part retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
from pathlib import Path
from typing import Any

import httpx
import truststore

from legalmind.access import Principal, Role
from legalmind.api.middleware.auth import dev_principal
from legalmind.retrieval.hybrid_search import HybridSearchEngine, SearchResult
from legalmind.services.azure_openai import AzureOpenAIService
from tests.evaluation._common import load_dataset, markdown_table, mean, normalise, write_report

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
MANIFEST = Path(__file__).resolve().parents[2] / "corpus" / "manifest.json"
TOP_K = 10

USERS: dict[str, dict[str, str]] = {
    "anna": {"X-Dev-User": "anna", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": "BM/2026/H-0412"},
    "bram": {"X-Dev-User": "bram", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": "BM/2026/A-0377"},
    "vera": {"X-Dev-User": "vera", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": "VRB/2024/ZK-9012"},
    "piet": {"X-Dev-User": "piet", "X-Dev-Roles": "Paralegal", "X-Dev-Matters": "VRB/2024/ZK-9012"},
    "guest": {"X-Dev-User": "guest"},
}
# Same question, two users: (question id, the file's user, an outsider)
SIDE_BY_SIDE = [("G31", "anna", "bram"), ("G37", "bram", "anna"), ("G13", "vera", "piet")]


def _documents() -> dict[str, dict[str, Any]]:
    """Corpus documents by title: their matter and privilege."""
    docs = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    return {d["title"]: {"matter": d.get("matter"), "privileged": d.get("privileged", False)} for d in docs}


def _matters(user: str) -> set[str]:
    return {m.strip() for m in USERS[user].get("X-Dev-Matters", "").split(",") if m.strip()}


def may_see(user: str, matter: str | None, privileged: bool) -> bool:
    """What the rules say this user may see (independent of the implementation)."""
    if matter is None:
        return True
    lawyer = Role.LAWYER in USERS[user].get("X-Dev-Roles", "").split(",")
    return matter in _matters(user) and (lawyer or not privileged)


def _rank(results: list[SearchResult], passage: str) -> int | None:
    needle = normalise(passage)
    return next((i for i, r in enumerate(results, 1) if needle in normalise(r.content)), None)


async def evaluate_retrieval_trimming() -> dict[str, Any]:
    docs = _documents()
    queries = load_dataset("retrieval_golden_set.json")
    engine = HybridSearchEngine(openai_service=AzureOpenAIService())
    everything = Principal.everything("eval-access")
    leaks: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for user, headers in USERS.items():
        principal = dev_principal(headers)
        hits_trimmed: list[float] = []
        hits_unrestricted: list[float] = []
        searched = 0
        for q in queries:
            source = docs[q["source"]]
            results = await engine.hybrid_search(q["query"], top=TOP_K, access=principal)
            searched += 1
            for r in results:
                if not may_see(user, r.matter, r.privileged):
                    leaks.append({"user": user, "query": q["id"], "title": r.title, "matter": r.matter})
            if may_see(user, source["matter"], source["privileged"]):
                rank = _rank(results, q["passage"])
                hits_trimmed.append(1.0 if rank and rank <= 5 else 0.0)
                full = await engine.hybrid_search(q["query"], top=TOP_K, access=everything)
                rank_full = _rank(full, q["passage"])
                hits_unrestricted.append(1.0 if rank_full and rank_full <= 5 else 0.0)
        rows.append(
            {
                "user": user,
                "matters": ", ".join(sorted(_matters(user))) or "-",
                "role": USERS[user].get("X-Dev-Roles", "-"),
                "searches": searched,
                "allowed questions": len(hits_trimmed),
                "hit@5 trimmed": mean(hits_trimmed),
                "hit@5 unrestricted": mean(hits_unrestricted),
                "leaked chunks": sum(1 for x in leaks if x["user"] == user),
            }
        )
        print(f"retrieval {user}: {rows[-1]}", flush=True)  # noqa: T201

    return {"rows": rows, "leaks": leaks, "searches": sum(r["searches"] for r in rows)}


async def _ask(client: httpx.AsyncClient, user: str, question: str) -> dict[str, Any]:
    response = await client.post(
        f"{API_URL}/api/chat", json={"message": question, "stream": False}, headers=USERS[user]
    )
    response.raise_for_status()
    reply: dict[str, Any] = response.json()
    return reply


def _outsider(matter: str) -> str:
    return "bram" if matter == "BM/2026/H-0412" else "anna"


async def evaluate_answer_trimming() -> dict[str, Any]:
    docs = _documents()
    questions = {q["id"]: q for q in load_dataset("generation_golden_set.json")}
    private = [q for q in questions.values() if q["answerable"] and docs.get(q["source"], {}).get("matter")]
    details: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(4)

    async with httpx.AsyncClient(timeout=240, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as client:

        async def outsider_asks(q: dict[str, Any]) -> None:
            source = docs[q["source"]]
            user = _outsider(source["matter"])
            async with semaphore:
                reply = await _ask(client, user, q["question"])
            text = normalise(reply["message"])
            facts = [any(normalise(v) in text for v in variants) for variants in q["must_contain"]]
            cited = {c["document_title"] for c in reply.get("citations", [])}
            forbidden = [
                t
                for t in cited
                if not may_see(user, docs.get(t, {}).get("matter"), bool(docs.get(t, {}).get("privileged")))
            ]
            details.append(
                {
                    "id": q["id"],
                    "user": user,
                    "file": q["source"],
                    "matter": source["matter"],
                    "forbidden_citations": forbidden,
                    "facts_given": sum(facts),
                    "facts_total": len(facts),
                    "all_facts_given": all(facts),
                    "app_grounding": reply.get("grounding"),
                    "answer": reply["message"],
                }
            )
            print(f"answers {q['id']} as {user}: facts {sum(facts)}/{len(facts)}, forbidden {forbidden}", flush=True)  # noqa: T201

        # One request first: concurrent cold requests each fetch credentials and can time out.
        await _ask(client, "guest", "Wat regelt artikel 7:271 BW?")
        await asyncio.gather(*(outsider_asks(q) for q in private))

        side_by_side = []
        for qid, insider, outsider in SIDE_BY_SIDE:
            q = questions[qid]
            for user in (insider, outsider):
                reply = await _ask(client, user, q["question"])
                side_by_side.append(
                    {
                        "id": qid,
                        "user": user,
                        "question": q["question"],
                        "cited": sorted({c["document_title"] for c in reply.get("citations", [])}),
                        "grounding": reply.get("grounding"),
                        "answer": reply["message"],
                    }
                )

    details.sort(key=lambda d: d["id"])
    return {"details": details, "side_by_side": side_by_side}


async def main(part: str) -> None:
    summary: dict[str, Any] = {}
    details: dict[str, Any] = {}
    md = ""

    if part in ("all", "retrieval"):
        retrieval = await evaluate_retrieval_trimming()
        summary["retrieval_searches"] = retrieval["searches"]
        summary["retrieval_leaked_chunks"] = len(retrieval["leaks"])
        details["retrieval"] = retrieval
        md += "## Retrieval, every golden question as every user\n\n" + markdown_table(retrieval["rows"]) + "\n"
        if retrieval["leaks"]:
            md += "### Leaks\n\n" + markdown_table(retrieval["leaks"]) + "\n"

    if part in ("all", "answers"):
        answers = await evaluate_answer_trimming()
        rows = answers["details"]
        summary["answers_asked_by_outsiders"] = len(rows)
        summary["answers_forbidden_citations"] = sum(len(d["forbidden_citations"]) for d in rows)
        summary["answers_all_facts_leaked"] = sum(1 for d in rows if d["all_facts_given"])
        summary["answers_some_fact_matched"] = sum(1 for d in rows if d["facts_given"])
        details["answers"] = answers
        md += (
            "## Case-file questions asked by someone not on the case\n\n"
            + markdown_table(
                [
                    {
                        "id": d["id"],
                        "user": d["user"],
                        "file (matter)": f"{d['file']} ({d['matter']})",
                        "forbidden citations": len(d["forbidden_citations"]),
                        "facts given": f"{d['facts_given']}/{d['facts_total']}",
                        "app label": d["app_grounding"],
                        "answer": d["answer"][:160],
                    }
                    for d in rows
                ]
            )
            + '\n`facts given` counts the golden facts found in the answer; a generic fact ("badkamer") can match '
            "without anything leaking, so read the answers.\n\n"
            "## Same question, two users\n\n"
            + markdown_table(
                [
                    {
                        "id": s["id"],
                        "user": s["user"],
                        "cited": "; ".join(s["cited"]) or "-",
                        "grounding": s["grounding"],
                        "answer": s["answer"][:220],
                    }
                    for s in answers["side_by_side"]
                ]
            )
        )

    md = "# Access evaluation: document-level security trimming\n\n" + markdown_table([summary]) + "\n" + md
    write_report("access", summary, [details], md)
    leaked = summary.get("retrieval_leaked_chunks", 0) + summary.get("answers_forbidden_citations", 0)
    if leaked:
        raise SystemExit(f"{leaked} leaks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--part", choices=["all", "retrieval", "answers"], default="all")
    asyncio.run(main(parser.parse_args().part))
