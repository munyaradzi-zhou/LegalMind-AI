"""Send realistic traffic to a deployed LegalMind API, to populate monitoring dashboards.

Simulates several users: multi-turn conversations about the sample documents, streamed
answers, direct searches, questions scoped to one document, inputs containing (synthetic)
personal data and abusive language for the guardrails, and a few invalid requests.

Paced to stay under the API's rate limit (60 requests/minute per client).

    python scripts/generate_traffic.py --api https://<api-url> [--rounds 2]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import ssl
import time
from collections import Counter
from typing import Any

import httpx
import truststore

CONVERSATIONS = [
    [
        "Wat dekt de rechtsbijstandverzekering precies?",
        "En hoe hoog is het eigen risico?",
        "Geldt er een wachttijd voor huurgeschillen?",
    ],
    [
        "Wat is er gebeurd in de zaak van de heer De Vries?",
        "Op welk wetsartikel baseren we de aansprakelijkheid van de werkgever?",
        "Schrijf een korte update voor de cliënt over de stand van zaken.",
    ],
    [
        "Welke termijn heeft de werkgever gekregen om te reageren op onze aansprakelijkstelling?",
        "Wat doen we als hij niet op tijd reageert?",
    ],
    [
        "Can I cancel the policy, and what notice period applies?",
        "And what if I don't pay the premium on time?",
    ],
    [
        "Voldoet het delen van medische gegevens van de cliënt met de werkgever aan de AVG?",
        "Welke documenten moeten we bij een schademelding aanleveren?",
    ],
]

SINGLE_QUESTIONS = [
    "Welke geschillen zijn uitgesloten van dekking?",
    "Mag ik zelf een advocaat kiezen?",
    "Waar kan ik terecht als mijn klacht niet wordt opgelost?",
    "Hoe is de schade in de zaak De Vries opgebouwd?",
    "Welke veiligheidsmiddelen ontbraken op de bouwplaats?",
    "Hoeveel vakantiedagen heeft de heer De Vries nog?",  # not in the documents
]

# Synthetic test values: 111222333 passes the BSN eleven-test; NL91ABNA0417164300 is the standard example IBAN.
PII_MESSAGES = [
    "Mijn BSN is 111222333 en mijn IBAN is NL91ABNA0417164300. Ben ik verzekerd voor een burenruzie?",
    "Bel me op 06-12345678 of mail naar j.devries@example.com over mijn zaak.",
]

ABUSIVE_MESSAGES = [
    "Als de werkgever niet betaalt, ga ik hem persoonlijk in elkaar slaan.",
]

SEARCHES = [
    {"query": "opzegtermijn", "top": 5},
    {"query": "valbeveiliging steiger", "top": 5, "document_type": "case_file"},
    {"query": "eigen risico dekking", "top": 3, "document_type": "contract"},
    {"query": "aansprakelijkheid werkgever 7:658", "top": 5, "use_query_rewriting": False},
]


class Traffic:
    def __init__(self, api: str, client: httpx.AsyncClient) -> None:
        self.api = api.rstrip("/")
        self.client = client
        self.stats: Counter[str] = Counter()
        self.latencies: list[float] = []

    async def _call(self, label: str, method: str, path: str, **kwargs: Any) -> httpx.Response | None:
        started = time.perf_counter()
        try:
            response = await self.client.request(method, f"{self.api}{path}", **kwargs)
        except httpx.HTTPError as exc:
            self.stats[f"{label}: error {type(exc).__name__}"] += 1
            return None
        self.latencies.append(time.perf_counter() - started)
        self.stats[f"{label}: {response.status_code}"] += 1
        await asyncio.sleep(1.2)  # pace: < 60 requests/minute
        return response

    async def chat(
        self, message: str, session_id: str | None = None, *, stream: bool = False, **extra: Any
    ) -> str | None:
        body = {"message": message, "session_id": session_id, "user_id": "traffic-generator", "stream": stream, **extra}
        if not stream:
            response = await self._call("chat", "POST", "/api/chat", json=body)
            return response.json().get("session_id") if response is not None and response.is_success else session_id
        started = time.perf_counter()
        new_session = session_id
        try:
            async with self.client.stream("POST", f"{self.api}/api/chat", json=body) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        event = json.loads(line[5:])
                        if event.get("done"):
                            new_session = event.get("session_id", session_id)
                self.stats[f"chat (stream): {response.status_code}"] += 1
        except httpx.HTTPError as exc:
            self.stats[f"chat (stream): error {type(exc).__name__}"] += 1
        self.latencies.append(time.perf_counter() - started)
        await asyncio.sleep(1.2)
        return new_session

    async def conversation(self, turns: list[str]) -> None:
        session = None
        for i, turn in enumerate(turns):
            session = await self.chat(turn, session, stream=(i % 2 == 1))

    async def run_round(self) -> None:
        await self._call("health", "GET", "/health")
        await self._call("agents", "GET", "/api/agents")
        for search in SEARCHES:
            await self._call("search", "POST", "/api/search", json=search)

        conversations = random.sample(CONVERSATIONS, k=len(CONVERSATIONS))
        await asyncio.gather(*(self.conversation(c) for c in conversations[:2]))
        await asyncio.gather(*(self.conversation(c) for c in conversations[2:]))

        for question in SINGLE_QUESTIONS:
            await self.chat(question)

        # A question scoped to one document (the IDs come from a search result).
        response = await self._call(
            "search", "POST", "/api/search", json={"query": "polisnummer verzekering", "top": 1}
        )
        if response is not None and response.is_success and response.json().get("results"):
            doc_id = response.json()["results"][0]["document_id"]
            await self.chat("Vat dit document samen in drie punten.", document_ids=[doc_id])

        for message in PII_MESSAGES + ABUSIVE_MESSAGES:
            await self.chat(message)

        # Invalid requests: show up as 4xx in the failure charts.
        await self._call("invalid", "POST", "/api/chat", json={"message": ""})
        await self._call("invalid", "POST", "/api/search", json={})
        await self._call("not found", "GET", "/api/does-not-exist")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", required=True, help="API base URL")
    parser.add_argument("--rounds", type=int, default=1, help="how many times to run the scenario set")
    args = parser.parse_args()

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=240, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as client:
        traffic = Traffic(args.api, client)
        for n in range(1, args.rounds + 1):
            print(f"Round {n}/{args.rounds} ...", flush=True)  # noqa: T201
            await traffic.run_round()

    latencies = sorted(traffic.latencies)
    print(f"\nDone in {time.perf_counter() - started:.0f}s, {len(latencies)} requests")  # noqa: T201
    if latencies:
        print(f"Latency p50 {latencies[len(latencies) // 2]:.1f}s, max {latencies[-1]:.1f}s")  # noqa: T201
    for key, count in sorted(traffic.stats.items()):
        print(f"  {key:<28} {count}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
