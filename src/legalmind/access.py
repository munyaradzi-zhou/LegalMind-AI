"""Who may see which documents: the signed-in principal and document-level security trimming.

Every indexed chunk carries ``allowed_groups``: the access groups that may retrieve it.
Public law (legislation, published rulings) carries ``"public"``; a client file carries
the Entra ID security group of its matter (case). A search only returns chunks whose
``allowed_groups`` overlap the caller's groups, and chunks marked ``privileged`` (legal
professional privilege) only reach users with the ``Lawyer`` role.

The filter is applied inside the search engine, not by the routes, so chat, ``/api/search``,
the agents' search tool and the MCP server can't forget it. With no principal the engine
refuses to search (fail closed).

Groups come from the token's ``groups`` claim (security group object IDs). Matters map
to groups with ``MATTER_GROUPS`` (JSON ``{"BM/2026/H-0412": "<group object id>"}``); a
matter without a mapping gets the tag ``matter:<number>``, which no Entra token carries,
so its documents stay hidden until the mapping exists.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum

from legalmind.config import get_settings

PUBLIC = "public"

# Group identifiers that are safe inside an OData string literal and a search.in() list.
_GROUP_ID = re.compile(r"^[A-Za-z0-9._:/@-]{1,128}$")


class Role(StrEnum):
    """App roles defined on the Entra ID app registration."""

    LAWYER = "Lawyer"
    PARALEGAL = "Paralegal"
    ADMIN = "Admin"


class AccessDeniedError(PermissionError):
    """The caller may not do this, or nobody established who the caller is."""


@dataclass(frozen=True)
class Principal:
    """The caller of a request: who they are, their app roles and their access groups."""

    user_id: str
    name: str = ""
    roles: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()
    # Sees every document. Only for local development and the quality evaluation,
    # never set from a token.
    unrestricted: bool = False

    @classmethod
    def everything(cls, user_id: str = "dev-user") -> Principal:
        return cls(user_id=user_id, name=user_id, roles=frozenset(Role), unrestricted=True)

    @property
    def can_ingest(self) -> bool:
        return Role.ADMIN in self.roles

    @property
    def sees_privileged(self) -> bool:
        return self.unrestricted or Role.LAWYER in self.roles

    def search_filter(self) -> str | None:
        """OData filter that limits a search to what this principal may see."""
        if self.unrestricted:
            return None
        groups = sorted({PUBLIC, *(g for g in self.groups if _GROUP_ID.match(g))})
        clause = f"allowed_groups/any(g: search.in(g, '{','.join(groups)}', ','))"
        if not self.sees_privileged:
            clause += " and privileged ne true"
        return clause


@dataclass(frozen=True)
class DocumentAccess:
    """Who may retrieve a document: public law, or the members of one matter's group."""

    matter: str | None = None  # None = public
    privileged: bool = False
    extra_groups: tuple[str, ...] = field(default=())

    @property
    def is_public(self) -> bool:
        return self.matter is None

    def allowed_groups(self) -> list[str]:
        if self.matter is None:
            return [PUBLIC]
        return sorted({matter_group(self.matter), *self.extra_groups})


def matter_group(matter: str) -> str:
    """The access group of a matter: its Entra group from MATTER_GROUPS, or a tag nobody holds."""
    return get_settings().matter_groups.get(matter) or f"matter:{matter}"


def combine_filters(*clauses: str | None) -> str | None:
    """AND the given OData clauses together, skipping empty ones."""
    present = [c for c in clauses if c]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return " and ".join(f"({c})" for c in present)


_current: ContextVar[Principal | None] = ContextVar("legalmind_principal", default=None)


def set_principal(principal: Principal | None) -> None:
    """Make ``principal`` the caller for the rest of this request (set by the auth middleware)."""
    _current.set(principal)


def current_principal() -> Principal:
    """The caller of the current request. Raises when nobody signed the request in."""
    principal = _current.get()
    if principal is None:
        raise AccessDeniedError("No signed-in principal for this request")
    return principal
