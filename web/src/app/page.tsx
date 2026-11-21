"use client";

import { useRef, useState, type FormEvent } from "react";

import { AnswerText } from "@/components/AnswerText";
import { GroundingBadge } from "@/components/GroundingBadge";
import { SignInGate } from "@/components/SignInGate";
import { SourcePanel } from "@/components/SourcePanel";
import { streamChat } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Citation } from "@/lib/citations";
import { STEPS } from "@/lib/grounding";

type Turn = {
  question: string;
  answer: string;
  step: string | null;
  citations: Citation[];
  grounding: string | null;
  agent: string | null;
  error: string | null;
};

export default function ChatPage() {
  return (
    <SignInGate>
      <Chat />
    </SignInGate>
  );
}

function Chat() {
  const { config, getToken } = useAuth();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState<Citation | null>(null);
  const sessionId = useRef<string | null>(null);

  // Changes the newest turn (the one being answered)
  const update = (patch: Partial<Turn> | ((turn: Turn) => Partial<Turn>)) =>
    setTurns((all) => {
      const last = all[all.length - 1];
      return [...all.slice(0, -1), { ...last, ...(typeof patch === "function" ? patch(last) : patch) }];
    });

  async function ask(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || !config || busy) return;
    setQuestion("");
    setBusy(true);
    setTurns((all) => [
      ...all,
      { question: text, answer: "", step: "checking_question", citations: [], grounding: null, agent: null, error: null },
    ]);
    try {
      const token = await getToken();
      await streamChat(config.apiUrl, token, text, sessionId.current, {
        onStatus: (step) => update({ step }),
        onText: (piece) => update((turn) => ({ step: null, answer: turn.answer + piece })),
        onDone: (answer) => {
          sessionId.current = answer.session_id;
          update({ step: null, citations: answer.citations, grounding: answer.grounding, agent: answer.agent_name });
        },
      });
    } catch (e) {
      update({ step: null, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {turns.length === 0 && (
        <section className="mb-8 rounded-lg bg-surface p-6">
          <h1 className="mb-2 font-serif text-3xl text-ink">Ask a legal question</h1>
          <p>
            Answers come from your matters&apos; documents, Dutch legislation (wetten.overheid.nl) and published case
            law (rechtspraak.nl). Every answer cites its sources and says whether it was checked against them.
          </p>
        </section>
      )}

      <ol className="space-y-6" aria-live="polite">
        {turns.map((turn, i) => (
          <li key={i} className="space-y-3">
            <p className="ml-auto max-w-[80%] rounded-lg bg-panel px-4 py-2 text-ink" data-testid="question">
              {turn.question}
            </p>
            <article className="rounded-lg bg-surface px-5 py-4" data-testid="answer">
              {turn.answer && <AnswerText text={turn.answer} citations={turn.citations} onCite={setSource} />}
              {turn.step && <p className="animate-pulse text-sm text-ink-muted">{STEPS[turn.step] ?? "Working…"}</p>}
              {turn.error && <p className="text-sm text-ember-deep">Something went wrong: {turn.error}</p>}
              <GroundingBadge grounding={turn.grounding} />
              {turn.citations.length > 0 && (
                <ul className="mt-3 flex flex-wrap gap-2 text-xs" aria-label="Sources">
                  {turn.citations.map((c) => (
                    <li key={c.index}>
                      <button
                        onClick={() => setSource(c)}
                        className="rounded border border-rule px-2 py-0.5 hover:border-ember-deep"
                      >
                        [{c.index}] {c.document_title}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {turn.agent && <p className="mt-2 text-xs text-ink-muted">Answered by {turn.agent.replace("_", " ")}</p>}
            </article>
          </li>
        ))}
      </ol>

      <form onSubmit={ask} className="sticky bottom-4 mt-8 flex gap-2 rounded-lg bg-surface p-2 shadow">
        <label htmlFor="question" className="sr-only">
          Your question
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) ask(e);
          }}
          rows={2}
          placeholder="Bijvoorbeeld: wat zegt artikel 7:271 BW over opzegging?"
          className="flex-1 resize-none rounded bg-paper-soft px-3 py-2 text-ink outline-none"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="rounded bg-ember-deep px-5 text-surface hover:bg-ember disabled:opacity-50"
        >
          Ask
        </button>
      </form>

      {source && <SourcePanel citation={source} onClose={() => setSource(null)} />}
    </>
  );
}
