"""Agent routing accuracy: does the orchestrator pick the right specialist?

Only the classification step runs (no agent answers), so this is fast and cheap.
Reports overall and per-agent accuracy, a confusion matrix and the mistakes.

    python -m tests.evaluation.evaluate_agents
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Any

from legalmind.agents.orchestrator import Orchestrator
from tests.evaluation._common import markdown_table, mean, percentile, write_report

AGENTS = ["document_analysis", "legal_research", "drafting", "compliance"]

# (user message, expected agent)
ROUTING_TEST_CASES = [
    ("Vat dit contract samen", "document_analysis"),
    ("Wat staat er in artikel 5?", "document_analysis"),
    ("Analyseer de clausules in dit document", "document_analysis"),
    ("Vergelijk deze twee polissen", "document_analysis"),
    ("Welke uitsluitingen staan er in de polisvoorwaarden?", "document_analysis"),
    ("Summarise the attached insurance policy", "document_analysis"),
    ("Zoek jurisprudentie over aansprakelijkheid bij arbeidsongeval", "legal_research"),
    ("Welke wetten zijn van toepassing op deze zaak?", "legal_research"),
    ("Zijn er vergelijkbare zaken geweest?", "legal_research"),
    ("Zoek precedenten voor contractbreuk", "legal_research"),
    ("Wat zegt artikel 7:658 BW over de zorgplicht van de werkgever?", "legal_research"),
    ("What does Dutch law say about the notice period for tenants?", "legal_research"),
    ("Schrijf een brief aan de tegenpartij", "drafting"),
    ("Stel een samenvatting op voor de cliënt", "drafting"),
    ("Maak een concept-antwoord op deze claim", "drafting"),
    ("Formuleer een reactie op dit bezwaarschrift", "drafting"),
    ("Schrijf een ingebrekestelling voor een huurder met huurachterstand", "drafting"),
    ("Draft a formal reply to the employer's insurer", "drafting"),
    ("Voldoet dit contract aan de AVG?", "compliance"),
    ("Controleer of dit document aan de regelgeving voldoet", "compliance"),
    ("Zijn er juridische risico's in deze overeenkomst?", "compliance"),
    ("Check de compliance van deze polisvoorwaarden", "compliance"),
    ("Mogen wij het BSN van de cliënt in deze e-mail opnemen?", "compliance"),
    ("Does this policy comply with the Wft information duties?", "compliance"),
]


async def evaluate_agent_routing() -> dict[str, Any]:
    orchestrator = Orchestrator()
    details: list[dict[str, Any]] = []
    for message, expected in ROUTING_TEST_CASES:
        started = time.perf_counter()
        decision = await orchestrator.classify_intent(message)
        details.append(
            {
                "message": message,
                "expected": expected,
                "actual": decision.intent.value,
                "confidence": round(decision.confidence, 2),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
        )

    correct = [d for d in details if d["actual"] == d["expected"]]
    summary = {
        "cases": len(details),
        "accuracy": len(correct) / len(details),
        "per_agent_accuracy": {
            agent: mean([1.0 if d["actual"] == agent else 0.0 for d in details if d["expected"] == agent])
            for agent in AGENTS
        },
        "mean_confidence_correct": mean([d["confidence"] for d in correct]),
        "mean_confidence_wrong": mean([d["confidence"] for d in details if d not in correct]),
        "latency_p50_ms": percentile([d["latency_ms"] for d in details], 50),
    }

    counts = Counter((d["expected"], d["actual"]) for d in details)
    actual_labels = AGENTS + sorted({d["actual"] for d in details} - set(AGENTS))
    matrix = [
        {"expected \\ routed to": exp, **{act: counts.get((exp, act), 0) for act in actual_labels}} for exp in AGENTS
    ]
    mistakes = [
        {"message": d["message"], "expected": d["expected"], "routed to": d["actual"], "confidence": d["confidence"]}
        for d in details
        if d["actual"] != d["expected"]
    ]
    md = (
        "# Agent routing evaluation\n\n"
        f"Accuracy: **{summary['accuracy']:.0%}** ({len(correct)}/{len(details)}), "
        f"median classification latency {summary['latency_p50_ms']:.0f} ms.\n\n"
        "## Per agent\n\n"
        + markdown_table([{"agent": a, "accuracy": v} for a, v in summary["per_agent_accuracy"].items()])
        + "\n## Confusion matrix\n\n"
        + markdown_table(matrix)
        + "\n## Mistakes\n\n"
        + markdown_table(mistakes)
    )
    write_report("agent_routing", summary, details, md)
    return summary


if __name__ == "__main__":
    asyncio.run(evaluate_agent_routing())
