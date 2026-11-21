import type { ReactNode } from "react";

import { splitCitations, type Citation } from "@/lib/citations";

/**
 * An answer with its [n] markers as buttons that open the cited source.
 * The model writes light Markdown: headings, **bold**, lists and rules are shown as such;
 * everything else stays plain text (no HTML from the model is ever rendered).
 */
export function AnswerText({
  text,
  citations,
  onCite,
}: {
  text: string;
  citations: Citation[];
  onCite: (citation: Citation) => void;
}) {
  const byNumber = new Map(citations.map((c) => [c.index, c]));

  const inline = (line: string, key: string): ReactNode[] =>
    line.split(/(\*\*[^*]+\*\*)/).flatMap((part, p) => {
      const bold = part.startsWith("**") && part.endsWith("**") && part.length > 4;
      const body = bold ? part.slice(2, -2) : part;
      const nodes = splitCitations(body).map((segment, i) => {
        const id = `${key}-${p}-${i}`;
        if ("text" in segment) return <span key={id}>{segment.text}</span>;
        const citation = byNumber.get(segment.cite);
        if (!citation) return <span key={id}>[{segment.cite}]</span>; // not among the shown sources
        return (
          <button
            key={id}
            onClick={() => onCite(citation)}
            className="mx-0.5 rounded bg-panel px-1 font-mono text-xs text-ember-deep hover:bg-ember-deep hover:text-surface"
            aria-label={`Source ${citation.index}: ${citation.document_title}`}
          >
            {citation.index}
          </button>
        );
      });
      return bold ? [<strong key={`${key}-${p}`} className="font-semibold text-ink">{nodes}</strong>] : nodes;
    });

  return (
    <div className="space-y-1">
      {text.split("\n").map((line, i) => {
        const key = `l${i}`;
        const heading = line.match(/^#{1,4}\s+(.*)$/);
        if (heading) {
          return (
            <h3 key={key} className="pt-2 font-serif text-lg font-semibold text-ink">
              {inline(heading[1], key)}
            </h3>
          );
        }
        if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) return <hr key={key} className="my-3 border-rule" />;
        if (!line.trim()) return <div key={key} className="h-2" />;
        return (
          <p key={key} className="whitespace-pre-wrap">
            {inline(line, key)}
          </p>
        );
      })}
    </div>
  );
}
