"""What the app tells the AI gateway about each model call (services/model_gateway.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2
import pytest

from legalmind.access import Principal, Role, set_principal
from legalmind.config import get_settings
from legalmind.services import model_gateway

if TYPE_CHECKING:
    from collections.abc import Iterator

HUUR = Principal("anna", roles=frozenset({Role.LAWYER}), groups=frozenset({"grp-huur"}))
HUUR_TOO = Principal("bram", roles=frozenset({Role.LAWYER}), groups=frozenset({"grp-huur"}))
ARBEID = Principal("vera", roles=frozenset({Role.LAWYER}), groups=frozenset({"grp-arbeid"}))
PARALEGAL_HUUR = Principal("piet", roles=frozenset({Role.PARALEGAL}), groups=frozenset({"grp-huur"}))


@pytest.fixture(autouse=True)
def no_principal() -> Iterator[None]:
    set_principal(None)
    yield
    set_principal(None)


def headers_for(principal: Principal | None) -> dict[str, str]:
    set_principal(principal)
    return model_gateway.caller_headers()


def test_calls_outside_a_request_are_the_service() -> None:
    assert headers_for(None) == {"x-legalmind-user": "service", "x-legalmind-access": "service"}


def test_each_user_is_counted_separately() -> None:
    assert headers_for(HUUR)["x-legalmind-user"] == "anna"
    assert headers_for(ARBEID)["x-legalmind-user"] == "vera"


def test_the_cache_partition_follows_what_a_user_may_see() -> None:
    # Same matters and roles: the same partition, so they can share cached answers
    assert headers_for(HUUR)["x-legalmind-access"] == headers_for(HUUR_TOO)["x-legalmind-access"]
    # Another matter, or no privileged documents: never the same partition
    assert headers_for(HUUR)["x-legalmind-access"] != headers_for(ARBEID)["x-legalmind-access"]
    assert headers_for(HUUR)["x-legalmind-access"] != headers_for(PARALEGAL_HUUR)["x-legalmind-access"]
    assert headers_for(HUUR)["x-legalmind-access"] != headers_for(Principal.everything())["x-legalmind-access"]


def test_without_the_gateway_the_sdk_keeps_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_VIA_GATEWAY", "false")
    get_settings.cache_clear()
    assert model_gateway.http_client() is None


async def test_through_the_gateway_every_request_carries_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_VIA_GATEWAY", "true")
    get_settings.cache_clear()
    client = model_gateway.http_client()
    assert client is not None
    set_principal(ARBEID)
    request = httpx2.Request("POST", "https://apim.example/openai/deployments/gpt-4.1-mini/chat/completions")
    for hook in client.event_hooks["request"]:
        await hook(request)
    assert request.headers["x-legalmind-user"] == "vera"
    assert len(request.headers["x-legalmind-access"]) == 16
    get_settings.cache_clear()
