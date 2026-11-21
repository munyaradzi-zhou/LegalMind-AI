# ADR 0003: Semantic Kernel for the specialist agents

- Status: accepted
- Date: 2026-11-21 (decided at the start of the project, recorded now)

## Context

LegalMind answers four kinds of question that need different instructions and tools:
what a document says (search and extraction), what the law says (search, summaries and,
since L7, the official legislation and case law), drafting a letter (templates,
translation) and compliance (GDPR, insurance regulation). One assistant with one long
prompt and every tool would be harder to steer and impossible to measure per kind of
question. The models are Azure OpenAI; the platform is Azure.

## Decision

A small orchestrator classifies each question (one gpt-4.1-mini call at temperature 0.1)
and hands it to one of four **specialist agents built with Semantic Kernel**
(`src/legalmind/agents/`). Each agent is a kernel with its own system prompt, few-shot
examples and plugins: Python methods marked `@kernel_function` that the model may call
(`FunctionChoiceBehavior.Auto`). Questions that fit no specialist are answered by the
orchestrator itself, with the retrieved context.

## Alternatives considered

| Option | Why not |
|---|---|
| One assistant, one prompt, all tools | Prompts for drafting and for research pull in different directions; no way to measure or fix one kind of question without affecting the others |
| LangChain / LangGraph | Capable, but a second abstraction over the same OpenAI calls; Semantic Kernel is Microsoft's SDK, uses Azure OpenAI and Entra ID natively and emits OpenTelemetry GenAI spans that land in Application Insights without glue |
| Azure AI Agent Service (hosted agents) | Agents and threads live in the service: less control over the prompt, tools and data path (security trimming happens in our search code), and it was in preview when this started |
| Plain OpenAI SDK with hand-written function calling | Workable for one agent; four agents with tools would re-implement what the kernel already does (tool schema from type hints, the call loop) |

## Consequences

- Routing is a separate, measurable step: `tests/evaluation/evaluate_agents.py` checks the
  intent for 24 messages. It found that document questions went to legal research
  (92% correct, 12 Nov); after a prompt fix 100% (24/24).
- Tools are plain Python methods: the L7 live sources (`LegalSourcesTool`) were added to
  the research agent and exposed over MCP from the same code.
- One extra model call per question for routing, and the agent's own tool calls: about
  3.3 model calls and 10K input tokens per answer (measured over 657 answers, 21 Nov).
- Semantic Kernel moves fast and pulls in pre-releases (`azure-ai-agents` beta for a
  security fix in `semantic-kernel` 1.39.4); dependency updates need the evaluation gate.
- The SDK client is passed in (`async_client`), which is how the AI gateway's per-user
  headers reach every call the agents make (ADR 0002).
