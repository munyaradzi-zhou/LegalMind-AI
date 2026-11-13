"""The orchestrator's own ("general") answers must see the retrieved documents too."""

from __future__ import annotations

from typing import Any

from legalmind.agents.orchestrator import Intent, Orchestrator


class FakeOpenAI:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def chat_completion(self, messages: list[dict[str, str]], **_: Any) -> dict[str, Any]:
        self.messages = messages
        return {"choices": [{"message": {"content": "antwoord [1]"}}]}


async def test_general_answer_receives_retrieved_documents() -> None:
    openai = FakeOpenAI()
    orchestrator = Orchestrator(openai_service=openai, agents={Intent.GENERAL: None})  # type: ignore[arg-type,dict-item]

    response = await orchestrator.process_message(
        "Wat is het eigen risico?",
        force_intent=Intent.GENERAL,
        additional_context={"retrieved_documents": "[1] Het eigen risico bedraagt €250 per geschil."},
    )

    prompt = "\n".join(m["content"] for m in openai.messages)
    assert "€250 per geschil" in prompt
    assert response.agent_response.agent_name == "orchestrator"


async def test_general_answer_without_context_still_works() -> None:
    openai = FakeOpenAI()
    orchestrator = Orchestrator(openai_service=openai, agents={Intent.GENERAL: None})  # type: ignore[arg-type,dict-item]

    response = await orchestrator.process_message("Hallo!", force_intent=Intent.GENERAL)

    assert [m["role"] for m in openai.messages] == ["system", "user"]
    assert response.agent_response.success
