import type { Citation } from "@/lib/citations";

/** The cited passage, its document and page, or a link to the official text for a live source. */
export function SourcePanel({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  return (
    <aside
      role="dialog"
      aria-label={`Source ${citation.index}`}
      className="fixed inset-y-0 right-0 z-10 w-full max-w-md overflow-y-auto border-l border-rule bg-surface p-6 shadow-xl"
    >
      <div className="mb-4 flex items-start justify-between gap-4">
        <h2 className="font-serif text-xl text-ink">
          [{citation.index}] {citation.document_title}
        </h2>
        <button onClick={onClose} className="text-ink-muted hover:text-ember-deep" aria-label="Close">
          ✕
        </button>
      </div>
      <p className="mb-4 text-xs text-ink-muted">
        {citation.url ? (
          <a href={citation.url} target="_blank" rel="noreferrer" className="text-ember-deep underline">
            {citation.document_id} (official text)
          </a>
        ) : (
          <>
            {citation.page_number ? `Page ${citation.page_number} · ` : ""}relevance {citation.relevance_score.toFixed(2)}
          </>
        )}
      </p>
      <blockquote className="whitespace-pre-wrap border-l-2 border-ember pl-4 text-sm">{citation.chunk_content}</blockquote>
    </aside>
  );
}
