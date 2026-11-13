"""LegalMind as an MCP server: lets any MCP-capable agent use the legal knowledge base.

The server is a thin client of the LegalMind REST API, so every call goes through the
same guardrails, audit logging and rate limiting as the web UI.

Tools: search_legal_documents, ask_legal_question, ingest_document, list_agents.

Run it:
    legalmind-mcp                                   # stdio (Claude Code, Claude Desktop, VS Code)
    legalmind-mcp --transport streamable-http       # HTTP on http://127.0.0.1:8001/mcp

Configuration (environment):
    LEGALMIND_API_URL    API base URL (default http://localhost:8000)
    LEGALMIND_API_TOKEN  optional bearer token, when the API requires Entra ID sign-in
"""

from __future__ import annotations

import argparse
import os
import ssl
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

DocumentType = Literal["contract", "correspondence", "case_file", "policy"]

INSTRUCTIONS = """\
LegalMind answers Dutch legal questions from an indexed knowledge base of contracts,
correspondence, case files and policies, with citations. Prefer ask_legal_question for
questions that need an answer, search_legal_documents to find passages, and pass the
returned session_id back to ask follow-up questions in the same conversation.
Answers are not legal advice and should be checked by a professional."""

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


mcp = FastMCP("LegalMind", instructions=INSTRUCTIONS)


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def list_agents() -> list[dict[str, Any]]:
    """List LegalMind's specialist agents, what each one does and which tools it uses."""
    data = await _request("GET", "/api/agents")
    agents: list[dict[str, Any]] = data if isinstance(data, list) else data.get("agents", [])
    return [{k: a.get(k) for k in ("name", "description", "tools")} for a in agents]


def main() -> None:
    parser = argparse.ArgumentParser(description="LegalMind MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
