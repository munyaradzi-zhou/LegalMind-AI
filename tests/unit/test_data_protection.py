"""Stored conversations hold no raw personal data: Cosmos only gets redacted text."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from legalmind.context.guardrails import GuardrailVerdict
from legalmind.services.content_safety import PiiDetectionResult, PiiEntity, apply_redaction
from tests.unit.test_access import LAWYER_HUUR, client, search, sessions, settings  # noqa: F401 - fixtures

if TYPE_CHECKING:
    import httpx
    import pytest


def _entity(text: str, whole: str, category: str) -> PiiEntity:
    return PiiEntity(text=text, category=category, subcategory=None, confidence=0.9,
                     offset=whole.index(text), length=len(text))  # fmt: skip


def _pii(whole: str, *found: tuple[str, str]) -> PiiDetectionResult:
    entities = [_entity(text, whole, category) for text, category in found]
    return PiiDetectionResult(entities=entities, entity_count=len(entities),
                              categories_found=sorted({c for _, c in found}), has_pii=bool(entities))  # fmt: skip


def test_entities_are_replaced_by_their_category() -> None:
    text = "Sandra de Wit woont op Lindenlaan 14-2, 3581 AB Utrecht."
    found = _pii(text, ("Sandra de Wit", "Person"), ("Lindenlaan 14-2, 3581 AB Utrecht", "Address"))
    assert apply_redaction(text, found.entities) == "[Person] woont op [Address]."


def test_overlapping_spans_are_merged_into_the_longest() -> None:
    text = "Adres: Lindenlaan 14-2, 3581 AB Utrecht"
    found = _pii(text, ("Lindenlaan 14-2, 3581 AB Utrecht", "Address"), ("3581 AB", "Address"), ("Utrecht", "City"))
    assert apply_redaction(text, found.entities) == "Adres: [Address]"


def test_a_dutch_identifier_wins_a_tie_with_ai_language() -> None:
    text = "BSN 111222333"
    language = _entity("111222333", text, "PhoneNumber")
    regex = _entity("111222333", text, "NLBSNNumber")  # found after AI Language's entities
    assert apply_redaction(text, [language, regex]) == "BSN [NLBSNNumber]"


async def test_chat_stores_redacted_messages_and_no_passages(
    client: httpx.AsyncClient,  # noqa: F811
    sessions: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from legalmind.api.routes import chat

    question = "Wat is de huurprijs van Sandra de Wit, BSN 111222333?"
    pii_in = _pii(question, ("Sandra de Wit", "Person"), ("111222333", "NLBSNNumber"))
    checked = SimpleNamespace(verdict=GuardrailVerdict.PASS, content_safety=None, groundedness=None)
    guardrails = MagicMock()
    guardrails.check_input = AsyncMock(return_value=SimpleNamespace(**vars(checked), pii_detection=pii_in))
    guardrails.check_output = AsyncMock(return_value=SimpleNamespace(**vars(checked), pii_detection=None))
    monkeypatch.setattr(chat, "ResponsibleAIGuardrails", lambda **_: guardrails)

    response = await client.post("/api/chat", json={"message": question, "stream": False}, headers=LAWYER_HUUR)
    assert response.status_code == 200, response.text
    assert response.json()["citations"][0]["chunk_content"]  # the user still gets the passages

    stored = sessions[response.json()["session_id"]]["messages"]
    assert stored[0]["content"] == "Wat is de huurprijs van [Person], BSN [NLBSNNumber]?"
    assert "Sandra" not in str(stored) and "111222333" not in str(stored)
    assert stored[1]["citations"] and all("chunk_content" not in c for c in stored[1]["citations"])
