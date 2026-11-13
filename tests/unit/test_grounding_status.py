"""Chat answers tell the user whether they are backed by the retrieved documents."""

from __future__ import annotations

from legalmind.api.routes.chat import cited_numbers, grounding_status, sources_used
from legalmind.config import Settings
from legalmind.context.guardrails import GroundednessResult


def test_no_context_means_no_documents() -> None:
    assert grounding_status("", None) == ("no_documents", None)


def test_verified_and_grounded() -> None:
    result = GroundednessResult(score=0.92, is_grounded=True)
    assert grounding_status("[1] passage", result) == ("documents", 0.92)


def test_claims_not_supported() -> None:
    result = GroundednessResult(score=0.3, is_grounded=False, ungrounded_claims=["7:672 BW: 3 months"])
    assert grounding_status("[1] passage", result) == ("not_grounded", 0.3)


def test_failed_check_is_unverified_not_grounded() -> None:
    result = GroundednessResult(score=0.0, is_grounded=False, verified=False)
    assert grounding_status("[1] passage", result) == ("unverified", None)


def test_cited_numbers_reads_all_marker_styles() -> None:
    answer = "De termijn is twee maanden [1]. Zie ook [2, 3] en [4][5]; artikel 7:672 BW [6; 7]."
    assert cited_numbers(answer) == {1, 2, 3, 4, 5, 6, 7}


def test_refusal_cites_nothing() -> None:
    assert cited_numbers("In de opgehaalde documenten is geen informatie te vinden over dit onderwerp.") == set()


def test_sources_used_combines_markers_and_judge() -> None:
    # Cites [2] by number and source 1 only as "(art. 12.1a)", which the judge recognised.
    result = GroundednessResult(score=1.0, is_grounded=True, used_sources=[1])
    assert sources_used("Twee maanden (art. 12.1a), zie ook [2].", result) == {1, 2}


def test_refusal_shows_no_sources() -> None:
    result = GroundednessResult(score=1.0, is_grounded=True, used_sources=[])
    assert sources_used("De documenten bevatten hierover geen informatie.", result) == set()


def test_without_a_verdict_all_sources_are_shown() -> None:
    assert sources_used("Antwoord zonder nummers.", None) is None
    assert sources_used("Antwoord.", GroundednessResult(score=0.0, is_grounded=False, verified=False)) is None


def test_query_rewriting_is_off_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("QUERY_REWRITING_ENABLED", raising=False)
    assert Settings(_env_file=None).query_rewriting_enabled is False  # type: ignore[call-arg]
