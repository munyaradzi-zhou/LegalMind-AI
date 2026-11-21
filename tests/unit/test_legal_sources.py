"""Live legal sources: parsing recorded responses, retries, caching, and how answers cite them.

The fixtures in fixtures/legal_sources are real responses, recorded on 21 November 2026 and
trimmed: the BWB search for BW Book 7, two of its articles, a rechtspraak.nl search feed and
one ruling.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from legalmind.agents.tools.legal_sources_tool import LegalSourcesTool
from legalmind.sources import _http, live, rechtspraak, wetten

if TYPE_CHECKING:
    from collections.abc import Iterator

FIXTURES = Path(__file__).parent / "fixtures" / "legal_sources"
BW7_XML = (
    "https://repository.officiele-overheidspublicaties.nl/bwb/BWBR0005290/2026-10-08_0/xml/BWBR0005290_2026-10-08_0.xml"
)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(autouse=True)
def fresh_caches() -> Iterator[None]:
    caches = (wetten.resolve, wetten._articles, rechtspraak._value_list, rechtspraak._recent, rechtspraak.get_ruling)
    for cached in caches:
        cached.cache_clear()  # type: ignore[attr-defined]
    live._current.set(None)
    yield
    live._current.set(None)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[tuple[str, str]] | None]]:
    """Serve the recorded responses instead of the internet; returns the calls made."""
    calls: list[tuple[str, list[tuple[str, str]] | None]] = []
    responses = {
        wetten.SRU: "sru_bw7.xml",
        BW7_XML: "bw7_extract.xml",
        f"{rechtspraak.API}/Waardelijst/Instanties": "instanties.xml",
        f"{rechtspraak.API}/Waardelijst/Rechtsgebieden": "rechtsgebieden.xml",
        f"{rechtspraak.API}/uitspraken/zoeken": "rechtspraak_feed.xml",
        f"{rechtspraak.API}/uitspraken/content": "ruling_rbrot_2025_1815.xml",
    }

    async def get(url: str, params: list[tuple[str, str]] | None = None) -> bytes:
        calls.append((url, params))
        return fixture(responses[url])

    for module in (wetten, rechtspraak):
        monkeypatch.setattr(module, "get", get)
    return calls


# ── References ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("law", "article", "expected"),
    [
        ("BW", "7:271", ("Burgerlijk Wetboek Boek 7", "271")),
        ("7:271 BW", "", ("Burgerlijk Wetboek Boek 7", "271")),
        ("bw", "art. 6:162", ("Burgerlijk Wetboek Boek 6", "162")),
        ("Burgerlijk Wetboek", "7:610", ("Burgerlijk Wetboek Boek 7", "610")),
        ("Awb", "8:69", ("Algemene wet bestuursrecht", "8:69")),  # Awb numbers are chapter:article
        ("Sr", "artikel 310", ("Wetboek van Strafrecht", "310")),
        ("Wet op het financieel toezicht", "1:1", ("Wet op het financieel toezicht", "1:1")),
    ],
)
def test_citations_resolve_to_a_law_and_an_article(law: str, article: str, expected: tuple[str, str]) -> None:
    assert wetten.parse_reference(law, article) == expected


def test_a_bw_article_needs_its_book() -> None:
    with pytest.raises(wetten.NotFoundError, match="book"):
        wetten.parse_reference("BW", "271")


# ── Legislation ────────────────────────────────────────────────────────────


async def test_an_article_comes_with_its_version_and_link(recorded: list[Any]) -> None:
    article = await wetten.get_article("BW", "7:271", on=date(2026, 11, 21))
    assert article.law.bwb_id == "BWBR0005290"
    assert article.law.version_date == "2026-10-08"
    assert article.in_force_since == "2024-07-01"
    assert article.label == "7:271 BW"
    assert article.text.startswith("1. In afwijking van artikel 228 lid 1 eindigt een voor bepaalde tijd")
    assert "\n2. Lid 1 is niet van toepassing" in article.text  # one line per paragraph (lid)
    assert "\xa0" not in article.text
    assert article.url.startswith("https://wetten.overheid.nl/jci1.0:c:BWBR0005290&artikel=271")
    query = dict(recorded[0][1])["query"]
    assert 'overheidbwb.titel="Burgerlijk Wetboek Boek 7"' in query
    assert "geldigheidsdatum==2026-11-21" in query


async def test_the_law_is_downloaded_once_for_many_articles(recorded: list[Any]) -> None:
    await wetten.get_article("BW", "7:271")
    await wetten.get_article("BW", "7:272")
    assert [url for url, _ in recorded].count(BW7_XML) == 1


async def test_a_missing_article_is_not_found(recorded: list[Any]) -> None:
    with pytest.raises(wetten.NotFoundError, match="no article 999"):
        await wetten.get_article("BW", "7:999")


async def test_a_similar_title_is_not_taken_for_the_law(recorded: list[Any]) -> None:
    # The title search is fuzzy; "Burgerlijk Wetboek Boek 7" must match exactly
    with pytest.raises(wetten.NotFoundError, match="Similar titles: Burgerlijk Wetboek Boek 7"):
        await wetten.resolve("Burgerlijk Wetboek Boek 77", "2026-11-21")


# ── Case law ───────────────────────────────────────────────────────────────


async def test_case_law_is_ranked_by_the_query(recorded: list[Any]) -> None:
    rulings = await rechtspraak.search_case_law("huurprijs gebreken woning", area="Verbintenissenrecht")
    assert rulings
    assert all("huur" in r.summary.lower() for r in rulings[:2])
    first = rulings[0]
    assert first.ecli.startswith("ECLI:NL:") and first.court and first.decided
    assert first.url == f"https://uitspraken.rechtspraak.nl/details?id={first.ecli}"
    params = dict(recorded[-1][1])
    assert params["subject"] == "http://psi.rechtspraak.nl/rechtsgebied#civielRecht_verbintenissenrecht"
    assert params["return"] == "DOC"  # only rulings with a published text


async def test_a_court_is_found_by_name(recorded: list[Any]) -> None:
    await rechtspraak.search_case_law("ontslag", court="Hoge Raad", since="2020-01-01")
    params = recorded[-1][1]
    assert ("creator", "http://standaarden.overheid.nl/owms/terms/Hoge_Raad_der_Nederlanden") in params
    assert ("date", "2020-01-01") in params


async def test_an_ambiguous_court_lists_the_options(recorded: list[Any]) -> None:
    with pytest.raises(rechtspraak.NotFoundError, match="Did you mean: rechtbank"):
        await rechtspraak.search_case_law("ontslag", court="Rechtbank")


async def test_a_ruling_by_ecli(recorded: list[Any]) -> None:
    ruling = await rechtspraak.get_ruling("ecli:nl:rbrot:2025:1815")
    assert ruling.ecli == "ECLI:NL:RBROT:2025:1815"
    assert ruling.court == "Rechtbank Rotterdam"
    assert ruling.decided == "2025-02-13"
    assert "Billijke vergoeding" in ruling.summary
    assert ruling.text


async def test_not_an_ecli_is_refused_without_a_call(recorded: list[Any]) -> None:
    with pytest.raises(rechtspraak.NotFoundError, match="not an ECLI"):
        await rechtspraak.get_ruling("7:271 BW")
    assert recorded == []


# ── HTTP: retries and the "unavailable" error ───────────────────────────────


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make every HTTP client answer with the statuses in the returned list, in order."""
    statuses: list[int] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/xml"
        status = statuses.pop(0) if statuses else 200
        return httpx.Response(status, content=b"<ok/>")

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(_http.httpx, "AsyncClient", client)
    monkeypatch.setattr(_http, "wait_exponential", lambda **_: lambda _state: 0)
    return statuses


async def test_a_server_error_is_retried(transport: list[int]) -> None:
    transport.extend([503, 429])
    assert await _http.get("https://example.test/x") == b"<ok/>"


async def test_a_source_that_stays_down_is_unavailable(transport: list[int]) -> None:
    transport.extend([503, 503, 503])
    with pytest.raises(_http.SourceUnavailableError, match=r"example\.test"):
        await _http.get("https://example.test/x")


async def test_a_client_error_is_not_retried(transport: list[int]) -> None:
    transport.extend([404, 200])
    with pytest.raises(_http.SourceUnavailableError):
        await _http.get("https://example.test/x")
    assert transport == [200]  # the second response was never asked for


async def test_results_are_cached_but_errors_are_not() -> None:
    calls = 0

    @_http.ttl_cache(60)
    async def lookup(key: str) -> int:
        nonlocal calls
        calls += 1
        if key == "bad":
            raise ValueError(key)
        return calls

    assert await lookup("a") == await lookup("a") == 1
    for _ in range(2):
        with pytest.raises(ValueError, match="bad"):
            await lookup("bad")
    assert calls == 3


# ── The tool: source numbers, citations, and saying when a source is down ──


async def test_the_tool_numbers_a_source_after_the_retrieved_passages(recorded: list[Any]) -> None:
    registry = live.start(first_number=9)  # passages [1]..[8] came from the index
    out = await LegalSourcesTool().get_article("BW", "7:271")
    assert out.startswith("Bron [9]: Artikel 7:271 BW")
    assert "Cite this source as [9]." in out
    again = await LegalSourcesTool().get_article("BW", "7:271")
    assert "Bron [9]" in again and len(registry.sources) == 1
    assert registry.context().startswith("[9] Artikel 7:271 BW")
    assert "(BWBR0005290 art. 271)" in registry.context()


async def test_rulings_get_consecutive_numbers(recorded: list[Any]) -> None:
    registry = live.start(first_number=4)
    out = await LegalSourcesTool().search_case_law("huurprijs gebreken", area="Verbintenissenrecht")
    numbers = [s.number for s in registry.sources]
    assert numbers == list(range(4, 4 + len(numbers)))
    assert all(f"Bron [{n}]" in out for n in numbers)
    assert all(s.identifier.startswith("ECLI:NL:") for s in registry.sources)


async def test_outside_a_chat_request_sources_are_cited_by_identifier(recorded: list[Any]) -> None:
    out = await LegalSourcesTool().get_ruling("ECLI:NL:RBROT:2025:1815")
    assert "Bron [" not in out and "(ECLI:NL:RBROT:2025:1815)" in out


async def test_a_source_that_is_down_is_reported_not_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*_: Any, **__: Any) -> bytes:
        raise _http.SourceUnavailableError("zoekservice.overheid.nl did not answer")

    monkeypatch.setattr(wetten, "get", down)
    registry = live.start(first_number=1)
    out = await LegalSourcesTool().get_article("BW", "7:271")
    assert out.startswith("SOURCE UNAVAILABLE: wetten.overheid.nl")
    assert "Do not quote this article from memory" in out
    assert registry.sources == []


async def test_not_found_is_said_plainly(recorded: list[Any]) -> None:
    assert (await LegalSourcesTool().get_article("BW", "271")).startswith("NOT FOUND:")
