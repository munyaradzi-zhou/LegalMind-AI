"""Semantic Kernel plugin: live Dutch legislation and case law (wetten.overheid.nl, rechtspraak.nl).

Used by the Research agent when a question needs the current text of a law or recent case
law, not only the uploaded documents. Each result is registered as a live source
(legalmind.sources.live): it gets a source number to cite, and the groundedness check
reads it like a retrieved passage. When a source is down the tool says so plainly, so the
model doesn't fill the gap from memory.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from legalmind.sources import live, rechtspraak, wetten
from legalmind.sources._http import SourceUnavailableError

logger = structlog.get_logger(__name__)

UNAVAILABLE = (
    "SOURCE UNAVAILABLE: {source} did not answer. Do not quote this {kind} from memory; "
    "tell the user the live source could not be reached and answer only from the documents."
)


def _cite(identifier: str, title: str, text: str, url: str) -> str:
    """Register the source and tell the model how to cite it."""
    registry = live.current()
    if registry is None:  # outside a chat request (MCP): cite by identifier
        return f"{title} ({identifier})\n{url}\n\n{text}"
    source = registry.add(identifier, title, text, url)
    return f"Bron [{source.number}]: {title} ({identifier})\n{url}\n\n{text}\n\nCite this source as [{source.number}]."


class LegalSourcesTool:
    """Live lookups in the official Dutch legal sources."""

    @kernel_function(
        name="get_article",
        description=(
            "Get the CURRENT official text of an article of Dutch legislation from wetten.overheid.nl, "
            "with the date its wording took effect. Use it whenever the user asks what an article says "
            "(today), or before relying on an article's exact wording. Examples: law='BW', article='7:271'; "
            "law='Awb', article='8:69'; law='Wetboek van Strafrecht', article='310'."
        ),
    )
    async def get_article(
        self,
        law: Annotated[str, "The law: an abbreviation (BW, Awb, Sr, Sv, Rv, Gw, UAVG, Wft) or its full title"],
        article: Annotated[str, "The article number; for the BW include the book, as in '7:271'"],
    ) -> Annotated[str, "The article's current text with its source number, or why it couldn't be found"]:
        logger.info("legal_source_get_article", law=law, article=article)
        today = date.today().isoformat()
        try:
            found = await wetten.get_article(law, article)
        except wetten.NotFoundError as exc:
            return f"NOT FOUND: {exc}"
        except SourceUnavailableError:
            return UNAVAILABLE.format(source="wetten.overheid.nl", kind="article")
        title = f"Artikel {found.label} (geldend op {today}; deze tekst geldt sinds {found.in_force_since})"
        return _cite(found.identifier, title, found.text, found.url)

    @kernel_function(
        name="search_case_law",
        description=(
            "Search recent published Dutch rulings on rechtspraak.nl by subject. Returns ECLI, court, date and "
            "the official summary of the best matches. Narrow it with a court ('Hoge Raad', 'Rechtbank Amsterdam', "
            "'Centrale Raad van Beroep') and/or an area of law ('Arbeidsrecht', 'Verbintenissenrecht', "
            "'Verzekeringsrecht' is not one: use 'Verbintenissenrecht'; also 'Bestuursrecht', 'Strafrecht', "
            "'Personen- en familierecht', 'Socialezekerheidsrecht')."
        ),
    )
    async def search_case_law(
        self,
        query: Annotated[str, "What the rulings should be about, in Dutch keywords"],
        court: Annotated[str, "Optional court name"] = "",
        area: Annotated[str, "Optional area of law (rechtsgebied)"] = "",
        since: Annotated[str, "Optional earliest ruling date, YYYY-MM-DD (default: three years ago)"] = "",
    ) -> Annotated[str, "Matching rulings with their source numbers"]:
        logger.info("legal_source_search_case_law", query=query[:100], court=court, area=area, since=since)
        try:
            rulings = await rechtspraak.search_case_law(query, court=court, area=area, since=since)
        except rechtspraak.NotFoundError as exc:
            return f"NOT FOUND: {exc}"
        except SourceUnavailableError:
            return UNAVAILABLE.format(source="rechtspraak.nl", kind="case law")
        if not rulings:
            return "No matching published rulings found. Try other keywords, or a court or area of law."
        return "\n\n---\n\n".join(
            _cite(r.ecli, f"{r.court}, {r.decided} ({r.case_number})", f"Samenvatting: {r.summary}", r.url)
            for r in rulings
        )

    @kernel_function(
        name="get_ruling",
        description="Get one published Dutch ruling by its ECLI (e.g. ECLI:NL:HR:2020:1946): summary and text.",
    )
    async def get_ruling(
        self,
        ecli: Annotated[str, "The ECLI identifier"],
    ) -> Annotated[str, "The ruling's summary and (the start of) its text, with its source number"]:
        logger.info("legal_source_get_ruling", ecli=ecli)
        try:
            ruling = await rechtspraak.get_ruling(ecli)
        except rechtspraak.NotFoundError as exc:
            return f"NOT FOUND: {exc}"
        except SourceUnavailableError:
            return UNAVAILABLE.format(source="rechtspraak.nl", kind="ruling")
        text = f"Samenvatting: {ruling.summary}\n\nUitspraak: {ruling.text}"
        return _cite(ruling.ecli, f"{ruling.court}, {ruling.decided} ({ruling.case_number})", text, ruling.url)
