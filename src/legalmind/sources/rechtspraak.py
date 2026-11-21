"""Dutch case law from the rechtspraak.nl open data API (data.rechtspraak.nl).

The API filters (court, area of law, date) but has no full-text search, so
``search_case_law`` fetches the most recent rulings that match the filters (up to 1000,
with their summaries) and ranks those summaries by the query's words. Give it a court or
an area of law: without one, the newest 1000 rulings of all courts are a thin slice.
``get_ruling`` fetches one ruling by its ECLI.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from defusedxml import ElementTree

from legalmind.sources._http import SourceUnavailableError, get, ttl_cache

API = "https://data.rechtspraak.nl"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_RS = "{http://www.rechtspraak.nl/schema/rechtspraak-1.0}"
_DC = "{http://purl.org/dc/terms/}"
_RDFS_LABEL = "{http://www.w3.org/2000/01/rdf-schema#}label"
_ECLI = re.compile(r"^ECLI:[A-Z]{2}:[A-Z0-9]{1,7}:\d{4}:[A-Z0-9.]{1,25}$", re.IGNORECASE)
HOUR = 3600

# Words that say nothing about a ruling's subject
_STOPWORDS = frozenset(
    {
        "de",
        "het",
        "een",
        "en",
        "of",
        "van",
        "in",
        "op",
        "te",
        "voor",
        "met",
        "aan",
        "bij",
        "door",
        "is",
        "zijn",
        "wordt",
        "worden",
        "niet",
        "als",
        "dat",
        "die",
        "dit",
        "deze",
        "er",
        "om",
        "uit",
        "naar",
        "over",
        "ook",
        "nog",
        "wel",
        "kan",
        "moet",
        "heeft",
        "hebben",
        "was",
        "waren",
        "welke",
        "wat",
        "wie",
        "hoe",
        "waar",
        "wanneer",
        "uitspraak",
        "uitspraken",
        "rechter",
        "rechtbank",
        "hof",
        "zaak",
        "zaken",
    }
)


class NotFoundError(LookupError):
    """No ruling with that ECLI, or an unknown court or area of law."""


@dataclass(frozen=True)
class Ruling:
    ecli: str
    court: str
    decided: str
    case_number: str
    summary: str
    text: str = ""  # the ruling itself (get_ruling only), shortened

    @property
    def url(self) -> str:
        return f"https://uitspraken.rechtspraak.nl/details?id={self.ecli}"


@ttl_cache(24 * HOUR)
async def _value_list(name: str) -> dict[str, str]:
    """A value list of the API ("Instanties", "Rechtsgebieden"): lowercase name -> identifier."""
    root = ElementTree.fromstring(await get(f"{API}/Waardelijst/{name}"))
    values: dict[str, str] = {}
    for item in root.iter():
        identifier, label = item.findtext("Identifier"), item.findtext("Naam")
        if identifier and label:
            values.setdefault(label.strip().lower(), identifier.strip())
    return values


async def _lookup(name: str, value: str) -> str:
    values = await _value_list(name)
    wanted = value.strip().lower()
    if wanted in values:
        return values[wanted]
    partial = sorted(label for label in values if wanted in label)
    if len(partial) == 1:
        return values[partial[0]]
    hint = f" Did you mean: {', '.join(partial[:6])}?" if partial else ""
    raise NotFoundError(f"Unknown {'court' if name == 'Instanties' else 'area of law'} {value!r}.{hint}")


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zà-ÿ0-9]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS]


def _score(query_terms: list[str], text: str) -> float:
    """Query words found in ``text``; a shared stem of 6+ letters counts (huurprijs ~ huurprijzen)."""
    words = Counter(_terms(text))
    score = 0.0
    for term in set(query_terms):
        if term in words:
            score += 1 + min(words[term], 3) * 0.1
        elif len(term) >= 6 and any(w.startswith(term[:6]) for w in words):
            score += 0.6
    return score


@ttl_cache(HOUR, maxsize=32)
async def _recent(court_id: str, area_id: str, since: str) -> list[Ruling]:
    params = [("type", "Uitspraak"), ("return", "DOC"), ("max", "1000"), ("sort", "DESC")]
    params += [("date", since), ("date", date.today().isoformat())]
    if court_id:
        params.append(("creator", court_id))
    if area_id:
        params.append(("subject", area_id))
    root = ElementTree.fromstring(await get(f"{API}/uitspraken/zoeken", params))
    rulings = []
    for entry in root.iterfind("a:entry", _ATOM):
        ecli = entry.findtext("a:id", "", _ATOM)
        # "ECLI:NL:HR:2026:1595, Hoge Raad, 02-10-2026, 25/0220"
        parts = [p.strip() for p in entry.findtext("a:title", "", _ATOM).split(",")]
        rulings.append(
            Ruling(
                ecli=ecli,
                court=parts[1] if len(parts) > 1 else "",
                decided=parts[2] if len(parts) > 2 else "",
                case_number=", ".join(parts[3:]),
                summary=(entry.findtext("a:summary", "", _ATOM) or "").strip(),
            )
        )
    return rulings


async def search_case_law(query: str, court: str = "", area: str = "", since: str = "", top: int = 5) -> list[Ruling]:
    """Recent rulings whose summary best matches ``query`` (court, area and since narrow it down)."""
    court_id = await _lookup("Instanties", court) if court else ""
    area_id = await _lookup("Rechtsgebieden", area) if area else ""
    start = since or (date.today() - timedelta(days=3 * 365)).isoformat()
    terms = _terms(query)
    scored = [(_score(terms, f"{r.summary} {r.court}"), r) for r in await _recent(court_id, area_id, start)]
    ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: s[0], reverse=True)
    return [r for _, r in ranked[:top]]


@ttl_cache(24 * HOUR, maxsize=64)
async def get_ruling(ecli: str, max_chars: int = 6000) -> Ruling:
    """One ruling: its summary and the start of the ruling itself."""
    ecli = ecli.strip().upper()
    if not _ECLI.match(ecli):
        raise NotFoundError(f"{ecli!r} is not an ECLI (like ECLI:NL:HR:2024:123)")
    try:
        root = ElementTree.fromstring(await get(f"{API}/uitspraken/content", [("id", ecli)]))
    except SourceUnavailableError as exc:
        if isinstance(exc.__cause__, Exception) and "404" in str(exc.__cause__):
            raise NotFoundError(f"No published ruling {ecli}") from exc
        raise

    def labelled(label: str) -> str:
        for element in root.iter():
            if element.get(_RDFS_LABEL) == label:
                return (element.text or "").strip()
        return ""

    def text_of(tag: str) -> str:
        element = root.find(f".//{_RS}{tag}")
        return re.sub(r"\s+", " ", "".join(element.itertext())).strip() if element is not None else ""

    body = text_of("uitspraak") or text_of("conclusie")
    return Ruling(
        ecli=ecli,
        court=root.findtext(f".//{_DC}creator", "").strip(),
        decided=labelled("Uitspraakdatum"),
        case_number=labelled("Zaaknr"),
        summary=text_of("inhoudsindicatie"),
        text=body[:max_chars] + (" [...]" if len(body) > max_chars else ""),
    )


__all__ = ["NotFoundError", "Ruling", "SourceUnavailableError", "get_ruling", "search_case_law"]
