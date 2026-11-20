"""Prompt versions come from the prompts' content."""

from __future__ import annotations

from legalmind import prompt_registry


def test_every_prompt_has_a_version() -> None:
    versions = prompt_registry.prompt_versions()
    assert len(versions) == 22
    assert all(len(v) == 12 for v in versions.values())
    assert len(set(versions.values())) == len(versions)  # no two prompts share a text


def test_a_changed_prompt_changes_its_version_and_the_fingerprint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    before = prompt_registry.prompt_versions()
    fingerprint = prompt_registry.prompt_fingerprint()
    sources = prompt_registry._sources()
    sources["orchestrator"] += " "
    monkeypatch.setattr(prompt_registry, "_sources", lambda: sources)
    prompt_registry.prompt_versions.cache_clear()
    prompt_registry.prompt_fingerprint.cache_clear()
    try:
        after = prompt_registry.prompt_versions()
        assert after["orchestrator"] != before["orchestrator"]
        assert {k: v for k, v in after.items() if k != "orchestrator"} == {
            k: v for k, v in before.items() if k != "orchestrator"
        }
        assert prompt_registry.prompt_fingerprint() != fingerprint
    finally:
        prompt_registry.prompt_versions.cache_clear()
        prompt_registry.prompt_fingerprint.cache_clear()
