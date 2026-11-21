"""Current text of Dutch legislation from wetten.overheid.nl (the BWB, Basiswettenbestand).

1. The law's title (or an abbreviation: BW, Awb, Sr, ...) is resolved to its BWB identifier
   and the version valid on the requested date, with the SRU search service of
   officiele-overheidspublicaties (``zoekservice.overheid.nl``, connection ``BWB``).
2. That version's XML (``repository.officiele-overheidspublicaties.nl``) is parsed once and
   cached; an article is looked up by its number.

"7:271 BW" means Burgerlijk Wetboek Boek 7, article 271: each book of the BW is its own law.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from defusedxml import ElementTree

from legalmind.sources._http import SourceUnavailableError, get, ttl_cache

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

SRU = "https://zoekservice.overheid.nl/sru/Search"
_NS = {
    "dcterms": "http://purl.org/dc/terms/",
    "overheidbwb": "http://standaarden.overheid.nl/bwb/terms/",
    "gzd": "http://standaarden.overheid.nl/sru",
}
DAY = 24 * 3600

# Abbreviations used in citations ("art. 6:162 BW", "artikel 8:69 Awb"), lowercase
ALIASES = {
    "bw": "Burgerlijk Wetboek Boek {book}",
    "awb": "Algemene wet bestuursrecht",
    "sr": "Wetboek van Strafrecht",
    "wvsr": "Wetboek van Strafrecht",
    "sv": "Wetboek van Strafvordering",
    "wvsv": "Wetboek van Strafvordering",
    "rv": "Wetboek van Burgerlijke Rechtsvordering",
    "wvk": "Wetboek van Koophandel",
    "gw": "Grondwet",
    "uavg": "Uitvoeringswet Algemene verordening gegevensbescherming",
    "wft": "Wet op het financieel toezicht",
}
_BWB_ID = re.compile(r"^BWB[RV]\d{7}$", re.IGNORECASE)
_BOOK_ARTICLE = re.compile(r"^(?P<book>\d{1,2}[a-z]?)\s*:\s*(?P<article>\d+[a-z]*)$", re.IGNORECASE)
_ARTICLE_WORD = re.compile(r"^(art(ikel)?\.?\s*)", re.IGNORECASE)


class NotFoundError(LookupError):
    """No such law or article (as opposed to the source being down)."""


@dataclass(frozen=True)
class Law:
    bwb_id: str
    title: str
    version_date: str  # the consolidated version (toestand) valid on the requested date
    xml_url: str


@dataclass(frozen=True)
class Article:
    law: Law
    number: str
    text: str
    in_force_since: str  # when this article's current wording took effect
    url: str

    @property
    def identifier(self) -> str:
        return f"{self.law.bwb_id} art. {self.number}"

    @property
    def label(self) -> str:
        """How lawyers cite it: "7:271 BW" for the books of the BW, else "310 Wetboek van Strafrecht"."""
        book = self.law.title.removeprefix("Burgerlijk Wetboek Boek ")
        if book != self.law.title:
            return f"{book}:{self.number} BW"
        return f"{self.number} {self.law.title}"


def parse_reference(law: str, article: str = "") -> tuple[str, str]:
    """("BW", "7:271"), ("7:271 BW", ""), ("Awb", "art. 8:69") -> (law title, article number)."""
    law, article = law.strip(), _ARTICLE_WORD.sub("", article.strip())
    if not article:  # "7:271 BW" or "artikel 271 Burgerlijk Wetboek Boek 7" in one string
        match = re.match(r"^(?:art(?:ikel)?\.?\s*)?(\d+[a-z]*(?::\d+[a-z]*)?)\s+(.+)$", law, re.IGNORECASE)
        if not match:
            raise NotFoundError(f"No article number in {law!r}")
        article, law = match.group(1), match.group(2)
    alias = ALIASES.get(law.lower().rstrip("."))
    book = _BOOK_ARTICLE.match(article)
    if alias and "{book}" in alias:
        if not book:
            raise NotFoundError(f"A BW article needs its book, as in 7:271 (got {article!r})")
        return alias.format(book=book["book"].upper()), book["article"]
    if book and not alias and re.fullmatch(r"burgerlijk wetboek", law, re.IGNORECASE):  # "Boek 7" left out
        return f"Burgerlijk Wetboek Boek {book['book'].upper()}", book["article"]
    # Awb and others number articles with a chapter prefix ("8:69"): that is the article number
    return alias or law, article


@ttl_cache(DAY)
async def resolve(title: str, on: str) -> Law:
    """The law called ``title`` (or with that BWB id), in the version valid on ``on``."""
    if _BWB_ID.match(title):
        query = f"dcterms.identifier=={title.upper()}"
    else:
        query = f'overheidbwb.titel="{title.replace(chr(34), "")}"'
    params = [
        ("operation", "searchRetrieve"),
        ("version", "2.0"),
        ("x-connection", "BWB"),
        ("maximumRecords", "50"),
        ("query", f"{query} and overheidbwb.geldigheidsdatum=={on}"),
    ]
    root = ElementTree.fromstring(await get(SRU, params))
    candidates = []
    for meta in root.iterfind(".//gzd:gzd", _NS):  # one per record: originalData and enrichedData
        law = Law(
            bwb_id=meta.findtext(".//dcterms:identifier", "", _NS),
            title=meta.findtext(".//dcterms:title", "", _NS),
            version_date=meta.findtext(".//overheidbwb:geldigheidsperiode_startdatum", "", _NS),
            xml_url=meta.findtext(".//overheidbwb:locatie_toestand", "", _NS),
        )
        if law.bwb_id and law.xml_url:
            candidates.append(law)
    # The title search is fuzzy ("Algemene wet bestuursrecht" also finds "Derde tranche ...")
    exact = [c for c in candidates if c.title.casefold() == title.casefold() or c.bwb_id == title.upper()]
    if exact:
        return exact[0]
    if candidates:
        options = ", ".join(sorted({c.title for c in candidates})[:5])
        raise NotFoundError(f"No law titled {title!r} valid on {on}. Similar titles: {options}")
    raise NotFoundError(f"No law titled {title!r} valid on {on}")


def _text(element: Element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext()).replace("\xa0", " ")).strip()


def _article_text(artikel: Element) -> str:
    members = artikel.findall("lid")
    if not members:
        return "\n".join(_text(al) for al in artikel.iter("al"))
    lines = []
    for lid in members:
        number = lid.findtext("lidnr", "").strip()
        body = " ".join(_text(part) for part in lid if part.tag not in ("lidnr", "meta-data"))
        lines.append(f"{number}. {body}" if number else body)
    return "\n".join(lines)


@ttl_cache(DAY, maxsize=8)  # a law's XML can be 4 MB: keep the parsed articles, for a few laws
async def _articles(xml_url: str) -> dict[str, tuple[str, str, str]]:
    """Article number -> (text, in force since, JCI reference), for one version of one law."""
    root = ElementTree.fromstring(await get(xml_url))
    articles: dict[str, tuple[str, str, str]] = {}
    for artikel in root.iter("artikel"):
        number = (artikel.findtext("kop/nr") or "").strip().lower()
        if not number or artikel.get("status") == "vervallen":
            continue
        jci = artikel.find("meta-data/jcis/jci")
        reference = jci.get("verwijzing", "") if jci is not None else ""
        articles.setdefault(number, (_article_text(artikel), artikel.get("inwerking", ""), reference))
    return articles


async def get_article(law: str, article: str = "", on: date | None = None) -> Article:
    """The text of ``article`` of ``law`` valid on ``on`` (today by default)."""
    title, number = parse_reference(law, article)
    day = (on or date.today()).isoformat()
    resolved = await resolve(title, day)
    found = (await _articles(resolved.xml_url)).get(number.lower())
    if found is None:
        raise NotFoundError(f"{resolved.title} has no article {number} on {day}")
    text, since, jci = found
    # A JCI reference ("1.0:c:BWBR0005290&artikel=271&g=...") redirects to the article itself
    url = (
        f"https://wetten.overheid.nl/jci{jci.removeprefix('jci')}"
        if jci
        else f"https://wetten.overheid.nl/{resolved.bwb_id}/{resolved.version_date}"
    )
    return Article(law=resolved, number=number, text=text, in_force_since=since, url=url)


__all__ = [
    "Article",
    "Law",
    "NotFoundError",
    "SourceUnavailableError",
    "get_article",
    "parse_reference",
    "resolve",
]
