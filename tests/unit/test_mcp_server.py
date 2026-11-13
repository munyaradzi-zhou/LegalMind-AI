"""MCP server: tools are registered and translate between MCP calls and the REST API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from legalmind import mcp_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Route the MCP server's HTTP calls to a fake API; returns the captured requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/search":
            return httpx.Response(
                200,
                json={
                    "query_used": "q",
                    "total_count": 1,
                    "results": [
                        {
                            "document_id": "d1",
                            "title": "polis.txt",
                            "document_type": "contract",
                            "page_number": 1,
                            "score": 0.03,
                            "reranker_score": 2.4,
                            "content": "Het eigen risico bedraagt €250 per geschil.",
                        }
                    ],
                },
            )
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": "€250 [1]",
                    "session_id": "s1",
                    "agent_name": "legal_research",
                    "grounding": "documents",
                    "groundedness_score": 1.0,
                    "citations": [
                        {
                            "index": 1,
                            "document_id": "d1",
                            "document_title": "polis.txt",
                            "chunk_content": "eigen risico",
                            "page_number": 1,
                            "relevance_score": 2.4,
                        }
                    ],
                },
            )
        if request.url.path == "/api/agents":
            return httpx.Response(200, json={"agents": [{"name": "drafting", "description": "d", "tools": ["draft"]}]})
        if request.url.path == "/api/ingest":
            return httpx.Response(200, json={"document_id": "d2", "chunk_count": 3, "status": "completed"})
        return httpx.Response(404, json={"detail": "not found"})

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))

    monkeypatch.setattr(mcp_server, "_client", client)
    return seen


async def test_tools_are_registered() -> None:
    names = {tool.name for tool in await mcp_server.mcp.list_tools()}
    assert names == {"search_legal_documents", "ask_legal_question", "ingest_document", "list_agents"}


async def test_search_maps_results_and_filters(api: list[httpx.Request]) -> None:
    results = await mcp_server.search_legal_documents("eigen risico", document_type="contract", top=50)
    assert results[0]["score"] == 2.4  # reranker score preferred
    assert results[0]["title"] == "polis.txt"
    sent = json.loads(api[0].content)
    assert sent == {"query": "eigen risico", "top": 20, "document_type": "contract"}  # top is capped


async def test_ask_returns_answer_session_and_citations(api: list[httpx.Request]) -> None:
    reply = await mcp_server.ask_legal_question("Eigen risico?", session_id="s0")
    assert reply["answer"] == "€250 [1]"
    assert reply["session_id"] == "s1"
    assert reply["citations"][0]["title"] == "polis.txt"
    assert reply["grounding"] == "documents"  # the calling agent can tell verified answers apart
    assert json.loads(api[0].content)["session_id"] == "s0"


async def test_ingest_uploads_file(api: list[httpx.Request], tmp_path: Path) -> None:
    doc = tmp_path / "brief.txt"
    doc.write_text("Geachte heer,", encoding="utf-8")
    result = await mcp_server.ingest_document(str(doc), document_type="correspondence")
    assert result["status"] == "completed"
    assert b'name="document_type"' in api[0].content
    assert b"brief.txt" in api[0].content


async def test_ingest_rejects_unsupported_type(tmp_path: Path) -> None:
    bad = tmp_path / "script.exe"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unsupported file type"):
        await mcp_server.ingest_document(str(bad))


async def test_api_errors_become_readable(api: list[httpx.Request], monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="returned 404"):
        await mcp_server._request("GET", "/api/missing")


async def test_list_agents(api: list[httpx.Request]) -> None:
    assert await mcp_server.list_agents() == [{"name": "drafting", "description": "d", "tools": ["draft"]}]
