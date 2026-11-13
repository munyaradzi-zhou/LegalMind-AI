"""Unit tests for token counting and context window management."""

from __future__ import annotations

import pytest

from legalmind.context.token_manager import TokenManager


@pytest.fixture
def token_manager() -> TokenManager:
    return TokenManager(max_context_tokens=8000, max_output_tokens=1000)


class TestCountTokens:
    """Tests for token counting."""

    def test_empty_string(self, token_manager: TokenManager):
        assert token_manager.count_tokens("") == 0

    def test_simple_english(self, token_manager: TokenManager):
        count = token_manager.count_tokens("Hello, world!")
        assert count > 0
        assert count < 10

    def test_dutch_legal_text(self, token_manager: TokenManager):
        text = (
            "De verzekerde is verplicht om binnen 30 dagen na het ontstaan"
            " van de schade een schademelding in te dienen."
        )
        count = token_manager.count_tokens(text)
        assert count > 10

    def test_longer_text_more_tokens(self, token_manager: TokenManager):
        short = "Kort."
        long = "Dit is een veel langere tekst die meer tokens zou moeten bevatten dan de korte tekst."
        assert token_manager.count_tokens(long) > token_manager.count_tokens(short)


class TestCountMessagesTokens:
    """Tests for message token counting."""

    def test_single_message(self, token_manager: TokenManager):
        messages = [{"role": "user", "content": "Hallo"}]
        count = token_manager.count_messages_tokens(messages)
        assert count > 0

    def test_multiple_messages(self, token_manager: TokenManager):
        messages = [
            {"role": "system", "content": "Je bent een juridisch assistent."},
            {"role": "user", "content": "Wat zijn de opzegvoorwaarden?"},
            {"role": "assistant", "content": "De opzegtermijn is twee maanden."},
        ]
        count = token_manager.count_messages_tokens(messages)
        # Should include per-message overhead
        assert count > sum(token_manager.count_tokens(m["content"]) for m in messages)

    def test_includes_formatting_overhead(self, token_manager: TokenManager):
        messages = [{"role": "user", "content": "Test"}]
        content_only = token_manager.count_tokens("Test") + token_manager.count_tokens("user")
        with_overhead = token_manager.count_messages_tokens(messages)
        assert with_overhead > content_only


class TestTruncateToBudget:
    """Tests for text truncation."""

    def test_short_text_not_truncated(self, token_manager: TokenManager):
        text = "Kort tekst."
        result = token_manager.truncate_to_budget(text, budget=100)
        assert result == text

    def test_long_text_truncated(self, token_manager: TokenManager):
        text = "Woord " * 500  # Many tokens
        result = token_manager.truncate_to_budget(text, budget=50)
        assert token_manager.count_tokens(result) <= 50 + 20  # Allow some margin for truncation notice
        assert "[... truncated" in result

    def test_truncation_preserves_start(self, token_manager: TokenManager):
        text = "BEGIN " + "midden " * 500 + " EINDE"
        result = token_manager.truncate_to_budget(text, budget=30)
        assert result.startswith("BEGIN")


class TestFitMessagesToBudget:
    """Tests for conversation history trimming."""

    def test_short_history_unchanged(self, token_manager: TokenManager):
        messages = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Vraag."},
        ]
        result = token_manager.fit_messages_to_budget(messages, budget=1000)
        assert len(result) == 2

    def test_long_history_trimmed(self, token_manager: TokenManager):
        messages = [{"role": "system", "content": "Prompt."}]
        for i in range(50):
            messages.append({"role": "user", "content": f"Vraag nummer {i} met extra tekst " * 10})
            messages.append({"role": "assistant", "content": f"Antwoord nummer {i} met extra tekst " * 10})

        result = token_manager.fit_messages_to_budget(messages, budget=500)
        assert len(result) < len(messages)
        # System message should be preserved
        assert result[0]["role"] == "system"

    def test_preserves_system_message(self, token_manager: TokenManager):
        messages = [
            {"role": "system", "content": "Belangrijk systeem prompt."},
            {"role": "user", "content": "Oude vraag. " * 100},
            {"role": "assistant", "content": "Oud antwoord. " * 100},
            {"role": "user", "content": "Nieuwe vraag."},
        ]
        result = token_manager.fit_messages_to_budget(messages, budget=200)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Belangrijk systeem prompt."


class TestBudgetReport:
    """Tests for budget reporting."""

    def test_report_structure(self, token_manager: TokenManager):
        report = token_manager.get_budget_report(
            system_prompt="System prompt tekst.",
            messages=[{"role": "user", "content": "Vraag."}],
            context="Context tekst voor retrieval.",
        )

        assert "system_prompt_tokens" in report
        assert "history_tokens" in report
        assert "context_tokens" in report
        assert "total_used" in report
        assert "remaining" in report
        assert "output_reserved" in report

    def test_report_totals_are_correct(self, token_manager: TokenManager):
        report = token_manager.get_budget_report(
            system_prompt="A",
            messages=[{"role": "user", "content": "B"}],
            context="C",
        )

        assert report["total_used"] == (
            report["system_prompt_tokens"] + report["history_tokens"] + report["context_tokens"]
        )
        assert report["remaining"] == report["total_available"] - report["total_used"]
