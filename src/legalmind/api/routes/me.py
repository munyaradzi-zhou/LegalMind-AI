"""Who the caller is, as the API sees them: for the web front end to show the right things."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from legalmind.access import Principal, matter_group
from legalmind.api.middleware.auth import get_principal
from legalmind.config import get_settings

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
async def me(principal: Principal = Depends(get_principal)) -> dict[str, Any]:  # noqa: B008
    """The signed-in user: roles, the matters they work on, and what the UI may offer them.

    The API still checks every request itself; this only saves the UI from offering an
    upload button that would be refused.
    """
    settings = get_settings()
    matters = sorted(m for m in settings.matter_groups if matter_group(m) in principal.groups)
    return {
        "user_id": principal.user_id,
        "name": principal.name,
        "roles": sorted(principal.roles),
        "matters": "all" if principal.unrestricted else matters,
        "can_ingest": principal.can_ingest,
        "sees_privileged": principal.sees_privileged,
        "sign_in_required": settings.requires_sign_in,
    }
