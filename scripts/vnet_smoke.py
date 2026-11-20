"""Smoke test from inside the VNet: the app works through the private endpoints.

Runs in the API image, next to the private endpoints (an Azure Container Instance in the
staging VNet, see docs in the L4 notes), with the environment's managed identity:

1. the services' host names resolve to private IPs
2. the API starts (it creates the search index)
3. a document with personal data is ingested, then a question about it is answered
4. the stored conversation in Cosmos DB holds the redacted text, not the personal data

Prints one JSON report to stdout (read it with ``az container logs``).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx

API = "http://127.0.0.1:8000"
NAME, BSN = "Sandra de Wit", "111222333"
DOCUMENT = (
    "HUUROVEREENKOMST WOONRUIMTE\n\nVerhuurder: Vastgoed Lindenhof B.V.\n"
    f"Huurder: {NAME}, BSN {BSN}\nAdres van het gehuurde: Lindenlaan 14-2, 3581 AB Utrecht\n\n"
    "Artikel 3. De kale huurprijs bedraagt EUR 1.185 per maand, te voldoen voor de eerste van de maand.\n"
)
QUESTION = f"Wat is de kale huurprijs die {NAME} (BSN {BSN}) per maand betaalt?"


def private_dns() -> dict[str, str]:
    hosts = {}
    for var in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_LANGUAGE_ENDPOINT",
        "AZURE_CONTENT_SAFETY_ENDPOINT",
        "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
        "AZURE_COSMOS_ENDPOINT",
        "AZURE_STORAGE_ACCOUNT_URL",
        "AZURE_KEYVAULT_URL",
    ):
        host = urlparse(os.environ[var]).hostname or ""
        hosts[host] = socket.gethostbyname(host)
    return hosts


async def stored_messages(session_id: str) -> list[dict[str, object]]:
    from legalmind.services.cosmos_client import get_cosmos_service

    cosmos = get_cosmos_service()
    try:
        return await cosmos.get_messages(session_id)
    finally:
        await cosmos.close()


def main() -> None:
    report: dict[str, object] = {"dns": private_dns()}
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "legalmind.main:app", "--port", "8000"])
    try:
        with httpx.Client(base_url=API, timeout=300) as client:
            for _ in range(60):
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(2)
            files = {"file": ("huurovereenkomst-de-wit.txt", DOCUMENT.encode(), "text/plain")}
            data = {"document_type": "contract", "matter": "BM/2026/H-0412", "title": "Huurovereenkomst De Wit (smoke)"}
            ingest = client.post("/api/ingest", files=files, data=data)
            report["ingest"] = {"status": ingest.status_code, "body": ingest.json()}
            time.sleep(5)  # indexing is near real time
            chat = client.post("/api/chat", json={"message": QUESTION, "stream": False})
            reply = chat.json()
            report["chat"] = {
                "status": chat.status_code,
                "answer": reply.get("message"),
                "grounding": reply.get("grounding"),
                "cited": [c["document_title"] for c in reply.get("citations", [])],
            }
            stored = asyncio.run(stored_messages(reply["session_id"]))
            report["stored_in_cosmos"] = [{"role": m.get("role"), "content": m.get("content")} for m in stored]
            report["stored_has_raw_pii"] = any(NAME in str(m) or BSN in str(m) for m in stored)
    finally:
        api.terminate()
    print("SMOKE_REPORT " + json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
