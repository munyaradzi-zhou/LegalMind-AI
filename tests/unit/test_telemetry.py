"""Telemetry the alerts and the workbook rely on: stage spans and answer attributes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from legalmind.telemetry import annotate_request, record_tokens, remember_request_span, stage, tracer
from tests.unit.test_access import LAWYER_HUUR, client, search, sessions, settings  # noqa: F401 - fixtures

if TYPE_CHECKING:
    import httpx

_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)  # once per process; legalmind's tracer is a proxy that picks it up


@pytest.fixture
def spans() -> InMemorySpanExporter:
    _exporter.clear()
    return _exporter


def test_answer_attributes_go_on_the_request_span(spans: InMemorySpanExporter) -> None:
    with tracer.start_as_current_span("POST /api/chat"):
        with stage("retrieval"):
            pass
        annotate_request(grounding="unverified", agent="research", citations=3, missing=None)
    request = next(s for s in spans.get_finished_spans() if s.name == "POST /api/chat")
    assert request.attributes == {"legalmind.grounding": "unverified", "legalmind.agent": "research",
                                  "legalmind.citations": 3}  # fmt: skip


def test_answer_attributes_skip_inner_framework_spans(spans: InMemorySpanExporter) -> None:
    with tracer.start_as_current_span("POST /api/chat"):
        remember_request_span()  # the middleware, outside FastAPI's own spans
        with tracer.start_as_current_span("fastapi.endpoint"):
            annotate_request(grounding="documents")
    by_name = {s.name: s.attributes for s in spans.get_finished_spans()}
    assert by_name["POST /api/chat"] == {"legalmind.grounding": "documents"}
    assert not by_name["fastapi.endpoint"]


def test_model_calls_carry_genai_token_counts(spans: InMemorySpanExporter) -> None:
    with tracer.start_as_current_span("chat gpt-4.1-mini") as span:
        record_tokens(span, "gpt-4.1-mini", 1200, 150)
    attrs = spans.get_finished_spans()[0].attributes or {}
    assert attrs["gen_ai.usage.input_tokens"] == 1200 and attrs["gen_ai.usage.output_tokens"] == 150


async def test_a_chat_request_records_each_stage(
    client: httpx.AsyncClient,  # noqa: F811
    sessions: dict[str, Any],  # noqa: F811
    spans: InMemorySpanExporter,
) -> None:
    response = await client.post("/api/chat", json={"message": "Huurprijs?", "stream": False}, headers=LAWYER_HUUR)
    assert response.status_code == 200
    names = [s.name for s in spans.get_finished_spans()]
    for expected in (
        "legalmind.guardrails.input",
        "legalmind.retrieval",
        "legalmind.agent",
        "legalmind.guardrails.output",
    ):
        assert expected in names
    retrieval = next(s for s in spans.get_finished_spans() if s.name == "legalmind.retrieval")
    assert (retrieval.attributes or {})["legalmind.results"] == 3
