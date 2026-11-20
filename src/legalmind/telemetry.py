"""Application telemetry: what each answer was like, and where its time went.

Lands in Application Insights (OpenTelemetry, configured in main.py) for the alerts and the
workbook in infra/modules/alerts.bicep:

- on the request (``requests.customDimensions``): ``legalmind.grounding``, ``legalmind.agent``,
  ``legalmind.citations``, ``legalmind.blocked``
- stage spans (``dependencies``, type InProc): ``legalmind.retrieval``, ``legalmind.agent``,
  ``legalmind.guardrails.input``, ``legalmind.guardrails.output``
- model calls: ``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens`` (the OpenTelemetry
  GenAI names; Semantic Kernel's own spans use the same, see SEMANTICKERNEL_EXPERIMENTAL_GENAI_*)

Without OpenTelemetry configured (tests, local runs) these are no-ops.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import Iterator

tracer = trace.get_tracer("legalmind")

# The request's server span. Inside a route the current span is FastAPI's internal
# "fastapi.endpoint" span, whose attributes don't reach the requests table, so the
# middleware remembers the outer one (see remember_request_span).
_request_span: ContextVar[trace.Span | None] = ContextVar("legalmind_request_span", default=None)


def remember_request_span() -> None:
    """Called by the outermost app middleware: the current span is the request's own."""
    _request_span.set(trace.get_current_span())


@contextmanager
def stage(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Time one stage of a request as its own span (``legalmind.<name>``)."""
    with tracer.start_as_current_span(f"legalmind.{name}", attributes=attributes) as span:
        yield span


def annotate_request(**attributes: Any) -> None:
    """Attach ``legalmind.<key>`` attributes to the request being served (``requests.customDimensions``)."""
    span = _request_span.get() or trace.get_current_span()
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(f"legalmind.{key}", value)


def record_tokens(span: trace.Span, model: str, input_tokens: int, output_tokens: int) -> None:
    span.set_attribute("gen_ai.request.model", model)
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
