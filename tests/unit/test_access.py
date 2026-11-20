"""Document-level security trimming: a user never retrieves, cites or sees another case's files.

The API runs in-process against a fake search index that applies the same OData filter
Azure AI Search would. Two test users work on different matters; the leakage tests go
through every way in: /api/search, /api/chat (citations and excerpts), the MCP tools,
and someone else's chat session.
"""

from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from legalmind import access, mcp_server
from legalmind.access import PUBLIC, AccessDeniedError, DocumentAccess, Principal, Role, combine_filters
from legalmind.api.middleware import auth
from legalmind.config import get_settings
from legalmind.ingestion.indexing import access_fields
from legalmind.retrieval.hybrid_search import HybridSearchEngine

if TYPE_CHECKING:
    from collections.abc import Iterator

HUUR = "BM/2026/H-0412"
ARBEID = "BM/2026/A-0377"

# One chunk per document: public law, two matters, and a privileged memo in the tenancy matter.
INDEX = [
    {"id": "wet_0", "document_id": "wet", "title": "BW 7 Huur", "content": "Art. 7:271 opzegtermijn huurder"},
    {"id": "huur_0", "document_id": "huur", "title": "Huurovereenkomst De Wit", "content": "Huurprijs €1.185"},
    {"id": "memo_0", "document_id": "memo", "title": "Processtrategie De Wit", "content": "Kans op winst 60%"},
    {"id": "arbeid_0", "document_id": "arbeid", "title": "Arbeidsovereenkomst Kramer", "content": "Salaris €3.450"},
]
ACCESS = {
    "wet": DocumentAccess(),
    "huur": DocumentAccess(matter=HUUR),
    "memo": DocumentAccess(matter=HUUR, privileged=True),
    "arbeid": DocumentAccess(matter=ARBEID),
}

LAWYER_HUUR = {"X-Dev-User": "anna", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": HUUR}
PARALEGAL_HUUR = {"X-Dev-User": "piet", "X-Dev-Roles": "Paralegal", "X-Dev-Matters": HUUR}
LAWYER_ARBEID = {"X-Dev-User": "bram", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": ARBEID}


def _indexed() -> list[dict[str, Any]]:
    docs = []
    for doc in INDEX:
        acc = ACCESS[doc["document_id"]]
        docs.append({**doc, **access_fields({"matter": acc.matter, "privileged": acc.privileged,
                                              "allowed_groups": acc.allowed_groups()})})  # fmt: skip
    return docs


_GROUPS = re.compile(r"allowed_groups/any\(g: search\.in\(g, '([^']*)', ','\)\)")


def matches(doc: dict[str, Any], odata: str | None) -> bool:
    """Evaluate the clauses LegalMind's filters use, the way Azure AI Search would."""
    if not odata:
        return True
    if m := _GROUPS.search(odata):
        if not set(m.group(1).split(",")) & set(doc["allowed_groups"]):
            return False
    elif "allowed_groups" in odata:
        raise AssertionError(f"unrecognised access filter: {odata}")
    return not ("privileged ne true" in odata and doc["privileged"] is True)


class FakeSearchService:
    def __init__(self) -> None:
        self.filters: list[str | None] = []

    async def hybrid_search(self, query: str, *, filters: str | None = None, top: int = 10, **_: Any) -> list[Any]:
        self.filters.append(filters)
        hits = [d for d in _indexed() if matches(d, filters)]
        return [{**d, "@search.score": 1.0 - i / 10} for i, d in enumerate(hits)][:top]

    async def vector_search(self, vector: list[float], *, filters: str | None = None, top: int = 10, **_: Any) -> Any:
        return await self.hybrid_search("", filters=filters, top=top)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://search.example")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def search(settings: Any, monkeypatch: pytest.MonkeyPatch) -> FakeSearchService:
    fake = FakeSearchService()
    openai = MagicMock()
    openai.generate_single_embedding = AsyncMock(return_value=[0.0] * 8)
    monkeypatch.setattr("legalmind.retrieval.hybrid_search.SearchService", lambda: fake)
    monkeypatch.setattr("legalmind.retrieval.hybrid_search.AzureOpenAIService", lambda: openai)
    rewriter = MagicMock()
    rewriter.rewrite_query = AsyncMock(side_effect=lambda q: SimpleNamespace(rewritten=q))
    monkeypatch.setattr("legalmind.api.routes.search.QueryRewriter", lambda: rewriter)
    return fake


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """In-memory sessions for the chat route, plus its other services stubbed out."""
    from legalmind.api.routes import chat
    from legalmind.context.guardrails import GuardrailVerdict

    store: dict[str, dict[str, Any]] = {}

    class Sessions:
        async def get_session(self, session_id: str) -> dict[str, Any] | None:
            return store.get(session_id)

        async def create_session(self, user_id: str, *, title: str | None = None) -> str:
            session_id = f"s{len(store) + 1}"
            store[session_id] = {"id": session_id, "user_id": user_id, "messages": []}
            return session_id

        async def add_message(self, session_id: str, role: str, content: str, **_: Any) -> None:
            store[session_id]["messages"].append({"role": role, "content": content})

        async def get_messages(self, session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
            return list(store[session_id]["messages"])

    class Orchestrator:
        def __init__(self, **_: Any) -> None: ...

        async def process_message(self, message: str, **kwargs: Any) -> Any:
            # Cites every passage it was given, so any leaked passage would show up.
            context = (kwargs.get("additional_context") or {}).get("retrieved_documents", "")
            numbers = sorted(set(re.findall(r"\[(\d+)\]", context)))
            return SimpleNamespace(
                routing=SimpleNamespace(intent=SimpleNamespace(value="research"), confidence=1.0, secondary_intents=[]),
                agent_response=SimpleNamespace(
                    success=True, content="Antwoord " + "".join(f"[{n}]" for n in numbers), agent_name="research"
                ),
            )

    allow = SimpleNamespace(verdict=GuardrailVerdict.PASS, content_safety=None, pii_detection=None, groundedness=None)
    guardrails = MagicMock()
    guardrails.check_input = AsyncMock(return_value=allow)
    guardrails.check_output = AsyncMock(return_value=allow)
    conversation = MagicMock()
    conversation.get_context_messages = MagicMock(return_value=[])
    conversation.maybe_summarize = AsyncMock()

    monkeypatch.setattr(chat, "SessionManager", Sessions)
    monkeypatch.setattr(chat, "Orchestrator", Orchestrator)
    openai = MagicMock()
    openai.generate_single_embedding = AsyncMock(return_value=[0.0] * 8)
    monkeypatch.setattr(chat, "AzureOpenAIService", lambda: openai)
    monkeypatch.setattr(chat, "ResponsibleAIGuardrails", lambda **_: guardrails)
    monkeypatch.setattr(chat, "AuditLogger", lambda: AsyncMock())
    monkeypatch.setattr(chat, "ConversationManager", lambda **_: conversation)
    return store


@pytest.fixture
def client(search: FakeSearchService) -> httpx.AsyncClient:
    from legalmind.main import create_app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://api")


async def _search_ids(client: httpx.AsyncClient, headers: dict[str, str]) -> set[str]:
    body = {"query": "huur", "top": 10, "use_query_rewriting": False}
    response = await client.post("/api/search", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return {r["document_id"] for r in response.json()["results"]}


# ─── The filter itself ───────────────────────────────────────────────────────


def test_filter_for_a_lawyer_covers_public_law_and_their_matters() -> None:
    lawyer = Principal("u1", roles=frozenset({Role.LAWYER}), groups=frozenset({"g-huur"}))
    assert lawyer.search_filter() == "allowed_groups/any(g: search.in(g, 'g-huur,public', ','))"


def test_filter_for_a_paralegal_excludes_privileged_documents() -> None:
    paralegal = Principal("u2", roles=frozenset({Role.PARALEGAL}), groups=frozenset({"g-huur"}))
    assert paralegal.search_filter().endswith(" and privileged ne true")  # type: ignore[union-attr]


def test_group_ids_that_could_break_out_of_the_filter_are_dropped() -> None:
    sneaky = Principal("u3", groups=frozenset({"x') or true or ('", "g-ok"}))
    assert "or true" not in (sneaky.search_filter() or "")
    assert "g-ok" in (sneaky.search_filter() or "")


def test_only_development_principals_are_unrestricted() -> None:
    assert Principal.everything().search_filter() is None
    assert auth.principal_from_claims({"oid": "u", "roles": ["Admin"]}).unrestricted is False


def test_documents_without_a_matter_are_public_and_matters_map_to_groups(settings: Any) -> None:
    assert DocumentAccess().allowed_groups() == [PUBLIC]
    assert DocumentAccess(matter=HUUR).allowed_groups() == [f"matter:{HUUR}"]  # no mapping: nobody's group
    settings.matter_groups[HUUR] = "6f1c-group"
    assert DocumentAccess(matter=HUUR).allowed_groups() == ["6f1c-group"]


def test_chunks_without_access_fields_are_visible_to_nobody() -> None:
    fields = access_fields({})
    assert fields == {"matter": None, "allowed_groups": [], "privileged": False}
    lawyer = Principal("u", roles=frozenset({Role.LAWYER}), groups=frozenset({f"matter:{HUUR}"}))
    assert not matches(fields, lawyer.search_filter())


def test_combine_filters() -> None:
    assert combine_filters(None, "a eq 1") == "a eq 1"
    assert combine_filters("x", None, "y") == "(x) and (y)"
    assert combine_filters(None, None) is None


async def test_search_engine_refuses_to_search_without_a_principal(search: FakeSearchService) -> None:
    access.set_principal(None)
    with pytest.raises(AccessDeniedError):
        await HybridSearchEngine().hybrid_search("huur")
    assert search.filters == []  # nothing reached the index


async def test_search_engine_uses_the_request_principal(search: FakeSearchService) -> None:
    access.set_principal(Principal("bram", roles=frozenset({Role.LAWYER}), groups=frozenset({f"matter:{ARBEID}"})))
    results = await HybridSearchEngine().hybrid_search("huur")
    assert {r.document_id for r in results} == {"wet", "arbeid"}
    access.set_principal(None)


async def test_agents_search_tool_is_trimmed_too(search: FakeSearchService) -> None:
    from legalmind.agents.tools.search_tool import SearchTool

    access.set_principal(Principal("anna", roles=frozenset({Role.PARALEGAL}), groups=frozenset({f"matter:{HUUR}"})))
    found = json.loads(await SearchTool().search_legal_documents("huurprijs", top_k=10))
    assert {r["title"] for r in found} == {"BW 7 Huur", "Huurovereenkomst De Wit"}  # no memo, no Kramer file
    access.set_principal(None)
    failed = json.loads(await SearchTool().search_legal_documents("huurprijs"))
    assert failed["results"] == []  # no principal: the tool gets an error, not unfiltered results


# ─── Leakage through the API ─────────────────────────────────────────────────


async def test_search_endpoint_returns_only_the_users_matters(client: httpx.AsyncClient) -> None:
    assert await _search_ids(client, LAWYER_HUUR) == {"wet", "huur", "memo"}
    assert await _search_ids(client, LAWYER_ARBEID) == {"wet", "arbeid"}


async def test_paralegal_does_not_see_privileged_documents(client: httpx.AsyncClient) -> None:
    assert await _search_ids(client, PARALEGAL_HUUR) == {"wet", "huur"}


async def test_user_without_matters_sees_public_law_only(client: httpx.AsyncClient) -> None:
    assert await _search_ids(client, {"X-Dev-User": "guest"}) == {"wet"}


async def test_chat_cites_only_documents_the_user_may_see(client: httpx.AsyncClient, sessions: dict[str, Any]) -> None:
    body = {"message": "Wat is de huurprijs en het salaris?", "stream": False}
    response = await client.post("/api/chat", json=body, headers=LAWYER_ARBEID)
    assert response.status_code == 200, response.text
    citations = response.json()["citations"]
    assert {c["document_id"] for c in citations} == {"wet", "arbeid"}
    assert not any("1.185" in c["chunk_content"] for c in citations)  # no excerpt of the tenancy file


async def test_chat_session_belongs_to_whoever_signed_in(client: httpx.AsyncClient, sessions: dict[str, Any]) -> None:
    body = {"message": "Huurprijs?", "stream": False, "user_id": "bram"}  # user_id in the body is ignored
    first = await client.post("/api/chat", json=body, headers=LAWYER_HUUR)
    session_id = first.json()["session_id"]
    assert sessions[session_id]["user_id"] == "anna"

    # Bram can't read or continue Anna's conversation (which quotes her client's file).
    stolen = await client.post("/api/chat", json={**body, "session_id": session_id}, headers=LAWYER_ARBEID)
    assert stolen.status_code == 404
    again = await client.post("/api/chat", json={**body, "session_id": session_id}, headers=LAWYER_HUUR)
    assert again.status_code == 200


async def test_mcp_tools_get_the_same_trimming(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def as_bram() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=client._transport, base_url="http://api", headers=LAWYER_ARBEID)

    monkeypatch.setattr(mcp_server, "_client", as_bram)
    passages = await mcp_server.search_legal_documents("huurprijs", top=10)
    assert {p["document_id"] for p in passages} == {"wet", "arbeid"}


# ─── Who may add documents ───────────────────────────────────────────────────


async def test_only_admins_can_ingest(client: httpx.AsyncClient) -> None:
    files = {"file": ("brief.txt", b"Geachte heer,", "text/plain")}
    response = await client.post("/api/ingest", files=files, data={"matter": HUUR}, headers=LAWYER_HUUR)
    assert response.status_code == 403


async def test_ingest_needs_an_explicit_matter(client: httpx.AsyncClient) -> None:
    admin = {"X-Dev-User": "root", "X-Dev-Roles": "Admin"}
    files = {"file": ("brief.txt", b"Geachte heer,", "text/plain")}
    response = await client.post("/api/ingest", files=files, headers=admin)
    assert response.status_code == 422
    public_privileged = {"matter": "public", "privileged": "true"}
    response = await client.post("/api/ingest", files=files, data=public_privileged, headers=admin)
    assert response.status_code == 400


# ─── Sign-in required (AUTH_ENABLED / production) ────────────────────────────


@pytest.fixture
def signing_key(settings: Any, monkeypatch: pytest.MonkeyPatch) -> rsa.RSAPrivateKey:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "azure_tenant_id", "tenant-1")
    monkeypatch.setattr(settings, "azure_api_audience", "api-client-id,api://legalmind")
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key()))
    monkeypatch.setattr(auth, "_jwks_client", lambda _: jwks)
    return key


def _token(key: rsa.RSAPrivateKey, **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://login.microsoftonline.com/tenant-1/v2.0",
        "aud": "api-client-id",
        "tid": "tenant-1",
        "oid": "user-anna",
        "iat": now,
        "exp": now + 600,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256")


def test_valid_token_gives_roles_and_groups(signing_key: rsa.RSAPrivateKey) -> None:
    claims = auth.validate_token(_token(signing_key, roles=["Lawyer"], groups=["g-huur"]))
    principal = auth.principal_from_claims(claims)
    assert principal.user_id == "user-anna"
    assert principal.roles == {"Lawyer"} and principal.groups == {"g-huur"}


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else"},
        {"iss": "https://login.microsoftonline.com/other-tenant/v2.0"},
        {"exp": int(time.time()) - 60},
    ],
)
def test_tokens_for_another_audience_issuer_or_expired_are_rejected(
    signing_key: rsa.RSAPrivateKey, claims: dict[str, Any]
) -> None:
    with pytest.raises(jwt.InvalidTokenError):
        auth.validate_token(_token(signing_key, **claims))


def test_group_overage_falls_back_to_public_law_only() -> None:
    claims = {"oid": "u", "roles": ["Lawyer"], "_claim_names": {"groups": "src1"}}
    assert auth.principal_from_claims(claims).groups == frozenset()


async def test_sign_in_required_rejects_missing_token_and_ignores_dev_headers(
    client: httpx.AsyncClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = await client.post("/api/search", json={"query": "huur"}, headers=LAWYER_HUUR)
    assert response.status_code == 401


async def test_signed_in_user_is_trimmed_by_token_groups(
    client: httpx.AsyncClient, signing_key: rsa.RSAPrivateKey, settings: Any
) -> None:
    settings.matter_groups[ARBEID] = "g-arbeid"
    token = _token(signing_key, roles=["Paralegal"], groups=["g-arbeid"])
    ids = await _search_ids(client, {"Authorization": f"Bearer {token}"})
    assert ids == {"wet", "arbeid"}  # their matter's file, without the tenancy file or privileged memo
