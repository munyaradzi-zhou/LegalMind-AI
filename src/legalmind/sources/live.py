"""Live sources an answer used: numbered like retrieved passages, cited, and checked.

The chat route starts a registry before the agent runs, numbering on from the retrieved
passages ([1]..[8], then [9], [10], ...). The legal source tools add what they fetched and
tell the model which number to cite. Afterwards the route adds them to the context the
groundedness check reads and to the answer's citations, like any retrieved passage.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LiveSource:
    number: int
    identifier: str  # "BWBR0005290 art. 271" or an ECLI
    title: str  # "Artikel 7:271 Burgerlijk Wetboek Boek 7 (geldend op 2026-11-21)"
    text: str
    url: str


@dataclass
class LiveSources:
    first_number: int
    sources: list[LiveSource] = field(default_factory=list)

    def add(self, identifier: str, title: str, text: str, url: str) -> LiveSource:
        """Register a fetched source (the same identifier twice keeps its first number)."""
        for source in self.sources:
            if source.identifier == identifier:
                return source
        source = LiveSource(self.first_number + len(self.sources), identifier, title, text, url)
        self.sources.append(source)
        return source

    def context(self) -> str:
        """The sources in the same "[n] title" form as the retrieved passages."""
        return "\n\n".join(f"[{s.number}] {s.title} ({s.identifier})\n{s.text}" for s in self.sources)


_current: ContextVar[LiveSources | None] = ContextVar("legalmind_live_sources", default=None)


def start(first_number: int) -> LiveSources:
    """Begin collecting live sources for this request (numbering from ``first_number``)."""
    sources = LiveSources(first_number)
    _current.set(sources)
    return sources


def current() -> LiveSources | None:
    """This request's registry, or None outside a chat request (e.g. a call over MCP)."""
    return _current.get()
