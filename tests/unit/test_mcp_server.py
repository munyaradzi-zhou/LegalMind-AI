"""MCP server: tools are registered and translate between MCP calls and the REST API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from legalmind import mcp_server

if TYPE_CHECKING:
    from collections.abc import Iterator
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

    async def client() -> httpx.AsyncClient:
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
    result = await mcp_server.ingest_document(str(doc), "BM/2026/H-0412", document_type="correspondence")
    assert result["status"] == "completed"
    assert b'name="document_type"' in api[0].content
    assert b"BM/2026/H-0412" in api[0].content  # the matter decides who may see it
    assert b"brief.txt" in api[0].content


async def test_ingest_rejects_unsupported_type(tmp_path: Path) -> None:
    bad = tmp_path / "script.exe"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unsupported file type"):
        await mcp_server.ingest_document(str(bad), "public")


async def test_api_errors_become_readable(api: list[httpx.Request], monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="returned 404"):
        await mcp_server._request("GET", "/api/missing")


async def test_list_agents(api: list[httpx.Request]) -> None:
    assert await mcp_server.list_agents() == [{"name": "drafting", "description": "d", "tools": ["draft"]}]


# ─── Remote mode (as deployed on Azure) ──────────────────────────────────────

KEY = "test-key-0123456789"


async def test_remote_server_leaves_out_ingest() -> None:
    names = {tool.name for tool in await mcp_server.build_server(remote=True).list_tools()}
    assert names == {"search_legal_documents", "ask_legal_question", "list_agents"}


def test_empty_api_key_is_refused() -> None:
    with pytest.raises(ValueError, match="empty API key"):
        mcp_server.RequireApiKey(lambda *_: None, "")  # type: ignore[arg-type]


async def _ok(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.parametrize(
    ("path", "headers", "status"),
    [
        ("/health", {}, 200),  # probes need no key
        ("/mcp", {}, 401),
        ("/mcp", {"Authorization": "Bearer wrong"}, 401),
        ("/mcp", {"Authorization": f"Bearer {KEY}"}, 200),
        ("/mcp", {"authorization": f"bearer {KEY}"}, 200),
        ("/mcp", {"X-API-Key": KEY}, 200),
    ],
)
async def test_api_key_is_required(path: str, headers: dict[str, str], status: int) -> None:
    app = mcp_server.RequireApiKey(_ok, KEY)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp") as client:
        response = await client.get(path, headers=headers)
    assert response.status_code == status
    if status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.fixture
def remote_server(api: list[httpx.Request]) -> Iterator[str]:
    """The remote app served by uvicorn on a free port, its tools talking to the fake API."""
    import socket
    import threading
    import time

    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp_server.remote_app(KEY), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


async def test_mcp_client_over_http(remote_server: str, api: list[httpx.Request]) -> None:
    """A real MCP client: connect with the key, list the tools, call one."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {KEY}"}
    async with (
        streamablehttp_client(f"{remote_server}/mcp", headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = {tool.name for tool in (await session.list_tools()).tools}
        result = await session.call_tool("search_legal_documents", {"query": "eigen risico"})

    assert "ingest_document" not in tools
    assert not result.isError
    assert "polis.txt" in result.content[0].text
    assert api[-1].url.path == "/api/search"


async def test_mcp_request_without_key_is_rejected() -> None:
    # In-process: some endpoint-security tools rewrite local HTTP responses on /mcp paths
    transport = httpx.ASGITransport(app=mcp_server.remote_app(KEY))
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


async def test_remote_server_accepts_a_public_host_name(remote_server: str) -> None:
    """Behind Azure's ingress the Host header is the public name; it must not get 421."""
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    }
    headers = {
        "Authorization": f"Bearer {KEY}",
        "Host": "ca-legalmind-mcp-dev.example.azurecontainerapps.io",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{remote_server}/mcp", json=initialize, headers=headers)
    assert response.status_code == 200
    assert "LegalMind" in response.text
