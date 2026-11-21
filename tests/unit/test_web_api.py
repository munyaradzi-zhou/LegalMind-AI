"""What the web front end relies on: the chat stream's events and /api/me."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from legalmind.config import get_settings
from tests.unit.test_access import HUUR, LAWYER_ARBEID, LAWYER_HUUR, client, search, sessions, settings  # noqa: F401

if TYPE_CHECKING:
    import httpx


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into (event, data) pairs."""
    parsed = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            parsed.append((fields.get("event", "message"), json.loads(fields["data"])))
    return parsed


async def stream(client: httpx.AsyncClient, body: dict[str, Any], headers: dict[str, str]) -> list[Any]:  # noqa: F811
    response = await client.post("/api/chat", json={**body, "stream": True}, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return events(response.text)


async def test_the_stream_reports_progress_then_the_answer(
    client: httpx.AsyncClient,  # noqa: F811
    sessions: dict[str, Any],  # noqa: F811
) -> None:
    sent = await stream(client, {"message": "Wat is de huurprijs?"}, LAWYER_HUUR)
    steps = [data["step"] for kind, data in sent if kind == "status"]
    assert steps == ["checking_question", "searching", "answering", "checking_answer"]
    pieces = [data for kind, data in sent if kind == "message"]
    assert "".join(p["content"] for p in pieces).startswith("Antwoord [")
    final = pieces[-1]
    assert final["done"] is True and final["session_id"] and final["agent_name"] == "research"
    assert final["grounding"] in ("documents", "unverified", "not_grounded", "no_documents")
    assert [c["document_title"] for c in final["citations"]]  # the passages it cited


async def test_another_users_session_is_an_error_event(
    client: httpx.AsyncClient,  # noqa: F811
    sessions: dict[str, Any],  # noqa: F811
) -> None:
    first = await stream(client, {"message": "Huurprijs?"}, LAWYER_HUUR)
    session_id = first[-1][1]["session_id"]
    sent = await stream(client, {"message": "En nu?", "session_id": session_id}, LAWYER_ARBEID)
    assert sent == [("error", {"status": 404, "detail": "Session not found"})]


@pytest.fixture
def matter_groups(monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:  # noqa: F811
    monkeypatch.setenv("MATTER_GROUPS", json.dumps({HUUR: HUUR, "BM/2026/A-0377": "grp-arbeid"}))
    get_settings.cache_clear()


async def test_me_for_a_lawyer(client: httpx.AsyncClient, matter_groups: None) -> None:  # noqa: F811
    me = (await client.get("/api/me", headers=LAWYER_HUUR)).json()
    assert me["user_id"] == "anna" and me["roles"] == ["Lawyer"]
    assert me["matters"] == [HUUR]
    assert me["can_ingest"] is False and me["sees_privileged"] is True
    assert me["sign_in_required"] is False


async def test_me_for_an_admin(client: httpx.AsyncClient) -> None:  # noqa: F811
    me = (await client.get("/api/me", headers={"X-Dev-User": "root", "X-Dev-Roles": "Admin"})).json()
    assert me["can_ingest"] is True and me["sees_privileged"] is False


async def test_me_without_dev_headers_is_the_unrestricted_dev_user(client: httpx.AsyncClient) -> None:  # noqa: F811
    me = (await client.get("/api/me")).json()
    assert me["matters"] == "all" and me["can_ingest"] is True
