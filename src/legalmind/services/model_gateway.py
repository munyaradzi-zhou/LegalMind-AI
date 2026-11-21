"""What the AI gateway (API Management, infra/modules/api-management.bicep) needs from each model call.

With ``AZURE_OPENAI_VIA_GATEWAY=true`` every call to Azure OpenAI carries:

- ``x-legalmind-user``: the signed-in user it is for (``service`` outside a request), so
  the gateway can limit and count tokens per user
- ``x-legalmind-access``: a hash of what that user may see (their search filter), so the
  semantic cache never answers one user from passages another user can't see

The gateway accepts calls only from the app's managed identity, so it can trust them.
"""

from __future__ import annotations

import hashlib
from functools import cache
from typing import TYPE_CHECKING

from openai import DefaultAsyncHttpxClient

from legalmind.access import AccessDeniedError, current_principal
from legalmind.config import get_settings

if TYPE_CHECKING:
    import httpx2  # the OpenAI SDK's HTTP client (an httpx fork)

SERVICE = "service"


def caller_headers() -> dict[str, str]:
    """Headers for the model call being made now, for the current request's user."""
    try:
        principal = current_principal()
    except AccessDeniedError:  # startup, ingestion jobs, scripts
        return {"x-legalmind-user": SERVICE, "x-legalmind-access": SERVICE}
    scope = principal.search_filter() or "everything"
    return {
        "x-legalmind-user": principal.user_id,
        "x-legalmind-access": hashlib.sha256(scope.encode()).hexdigest()[:16],
    }


async def _add_caller(request: httpx2.Request) -> None:
    request.headers.update(caller_headers())


def http_client() -> DefaultAsyncHttpxClient | None:
    """The HTTP client for the OpenAI SDK: None (the SDK's default) unless calls go through the gateway."""
    return _gateway_client() if get_settings().openai.via_gateway else None


@cache  # one connection pool for every model client (agents are created per request)
def _gateway_client() -> DefaultAsyncHttpxClient:
    # The SDK's client with its own timeouts and pool, plus the caller headers
    return DefaultAsyncHttpxClient(event_hooks={"request": [_add_caller]})
