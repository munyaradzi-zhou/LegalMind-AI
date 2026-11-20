"""LegalMind as an MCP server: lets any MCP-capable agent use the legal knowledge base.

The server is a thin client of the LegalMind REST API, so every call goes through the
same guardrails, audit logging and rate limiting as the web UI.

Tools: search_legal_documents, ask_legal_question, ingest_document, list_agents.

Run it:
    legalmind-mcp                                   # stdio (Claude Code, Claude Desktop, VS Code)
    legalmind-mcp --transport streamable-http       # HTTP on http://127.0.0.1:8001/mcp
    legalmind-mcp --transport streamable-http --remote --host 0.0.0.0   # as deployed on Azure

Remote mode is for other people's agents: every request needs the API key
(``Authorization: Bearer <key>`` or ``X-API-Key``), sessions are stateless so the server
can scale out, ``GET /health`` answers without a key for the platform's probes, and
ingest_document is left out (it reads files from the caller's own disk).

Configuration (environment):
    LEGALMIND_API_URL      API base URL (default http://localhost:8000)
    LEGALMIND_API_TOKEN    optional bearer token, when the API requires Entra ID sign-in
    LEGALMIND_MCP_API_KEY  the key remote clients must send (required with --remote)
"""

from __future__ import annotations

import argparse
import hmac
import os
import ssl
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request
    from starlette.types import ASGIApp, Receive, Scope, Send

DocumentType = Literal["legislation", "court_filing", "contract", "correspondence", "case_file", "policy", "general"]

INSTRUCTIONS = """\
LegalMind answers Dutch legal questions with citations, from an indexed knowledge base of
Dutch legislation (Burgerlijk Wetboek Boek 7: tenancy, employment, insurance), court rulings,
and a law firm's case files (contracts, correspondence, scanned letters and forms).
Prefer ask_legal_question for questions that need an answer, search_legal_documents to find
passages, and pass the returned session_id back to ask follow-up questions in the same
conversation. Check `grounding` before relying on an answer. Answers are not legal advice
and should be checked by a professional."""

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
}


def _verify() -> ssl.SSLContext | bool:
    """Use the OS certificate store when available (works behind TLS-inspecting proxies)."""
    try:
        import truststore
    except ImportError:
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _client() -> httpx.AsyncClient:
    headers = {"User-Agent": "legalmind-mcp"}
    if token := os.getenv("LEGALMIND_API_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        base_url=os.getenv("LEGALMIND_API_URL", "http://localhost:8000").rstrip("/"),
        headers=headers,
        timeout=300,
        verify=_verify(),
    )


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    async with _client() as client:
        response = await client.request(method, path, **kwargs)
    if response.is_error:
        # A readable error for the calling agent instead of a stack trace.
        raise RuntimeError(f"LegalMind API returned {response.status_code}: {response.text[:300]}")
    return response.json()


async def search_legal_documents(
    query: str,
    document_type: DocumentType | None = None,
    top: int = 5,
) -> list[dict[str, Any]]:
    """Search the legal knowledge base (hybrid keyword + vector search with semantic ranking).

    Returns the best-matching passages with their document, type, page and score.
    Use it to find where something is written; use ask_legal_question for an answer.
    """
    body: dict[str, Any] = {"query": query, "top": max(1, min(top, 20))}
    if document_type:
        body["document_type"] = document_type
    data = await _request("POST", "/api/search", json=body)
    return [
        {
            "document_id": r.get("document_id"),
            "title": r.get("title"),
            "document_type": r.get("document_type"),
            "page": r.get("page_number"),
            "score": r.get("reranker_score") or r.get("score"),
            "content": r.get("content"),
        }
        for r in data.get("results", [])
    ]


async def ask_legal_question(
    question: str,
    session_id: str | None = None,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Ask LegalMind a legal question and get a grounded answer with numbered citations.

    A specialist agent (document analysis, legal research, drafting or compliance) answers
    from the knowledge base. Pass the returned session_id to ask a follow-up question in the
    same conversation, and document_ids to restrict the answer to specific documents.

    `grounding` says whether the answer is backed by the documents: "documents" (verified),
    "not_grounded" (some statements not supported - tell the user), "unverified" (the check
    failed) or "no_documents" (nothing relevant was found).
    """
    body = {
        "message": question,
        "session_id": session_id,
        "user_id": "mcp-client",
        "stream": False,
        "document_ids": document_ids or None,
    }
    data = await _request("POST", "/api/chat", json=body)
    return {
        "answer": data["message"],
        "agent": data.get("agent_name"),
        "grounding": data.get("grounding"),
        "groundedness_score": data.get("groundedness_score"),
        "session_id": data.get("session_id"),
        "citations": [
            {
                "index": c["index"],
                "document_id": c["document_id"],
                "title": c["document_title"],
                "page": c.get("page_number"),
                "excerpt": c.get("chunk_content"),
            }
            for c in data.get("citations", [])
        ],
    }


async def ingest_document(
    file_path: str,
    document_type: DocumentType = "contract",
    title: str | None = None,
    language: str = "nl",
) -> dict[str, Any]:
    """Add a local file (PDF, DOCX, TXT or image) to the knowledge base.

    The document is stored, analysed, chunked, enriched with entities, embedded and indexed.
    Returns the new document_id, the number of chunks and the status.
    """
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("File is larger than 50 MB")
    content_type = CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise ValueError(f"Unsupported file type {path.suffix}; use one of {', '.join(CONTENT_TYPES)}")
    form = {"document_type": document_type, "language": language}
    if title:
        form["title"] = title
    files = {"file": (path.name, path.read_bytes(), content_type)}
    data: dict[str, Any] = await _request("POST", "/api/ingest", data=form, files=files)
    return data


async def list_agents() -> list[dict[str, Any]]:
    """List LegalMind's specialist agents, what each one does and which tools it uses."""
    data = await _request("GET", "/api/agents")
    agents: list[dict[str, Any]] = data if isinstance(data, list) else data.get("agents", [])
    return [{k: a.get(k) for k in ("name", "description", "tools")} for a in agents]


def build_server(*, remote: bool = False) -> FastMCP:
    """The MCP server with its tools; ``remote`` leaves out tools that need the caller's disk."""
    server = FastMCP(
        "LegalMind",
        instructions=INSTRUCTIONS,
        # Each request stands alone, so any replica can serve any client
        stateless_http=remote,
    )
    tools: list[Callable[..., Any]] = [search_legal_documents, ask_legal_question, list_agents]
    if not remote:
        tools.append(ingest_document)
    for tool in tools:
        server.add_tool(tool)
    server.custom_route("/health", methods=["GET"])(_health)
    return server


async def _health(_: Request) -> JSONResponse:
    """Liveness for the platform's probes; needs no API key."""
    return JSONResponse({"status": "ok"})


mcp = build_server()


class RequireApiKey:
    """ASGI middleware: every request except ``GET /health`` must carry the API key."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        if not api_key:
            raise ValueError("An empty API key would let everyone in")
        self.app = app
        self._key = api_key.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        bearer = headers.get(b"authorization", b"")
        offered = bearer[7:] if bearer[:7].lower() == b"bearer " else headers.get(b"x-api-key", b"")
        if hmac.compare_digest(offered, self._key):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": "unauthorized", "detail": "Send the API key as 'Authorization: Bearer <key>' or 'X-API-Key'."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


def remote_app(api_key: str) -> ASGIApp:
    """The streamable-HTTP app for other people's agents, behind the API key."""
    return RequireApiKey(build_server(remote=True).streamable_http_app(), api_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="LegalMind MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--remote", action="store_true", help="API key required, stateless, no ingest tool")
    args = parser.parse_args()
    if args.remote:
        if args.transport != "streamable-http":
            parser.error("--remote needs --transport streamable-http")
        api_key = os.getenv("LEGALMIND_MCP_API_KEY", "")
        if not api_key:
            parser.error("--remote needs LEGALMIND_MCP_API_KEY")
        import uvicorn

        uvicorn.run(remote_app(api_key), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")
        return
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
