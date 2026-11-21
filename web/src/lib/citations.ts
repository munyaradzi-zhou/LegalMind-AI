// Answers cite sources as [1], [2, 3] or [4][5]; the UI turns those into buttons.

export type Citation = {
  index: number;
  document_id: string;
  document_title: string;
  chunk_content: string;
  page_number: number | null;
  relevance_score: number;
  url?: string | null; // a live source: wetten.overheid.nl or rechtspraak.nl
};

export type Segment = { text: string } | { cite: number };

const MARKER = /\[(\d+(?:\s*[,;]\s*\d+)*)\]/g;

/** Split an answer into text and citation numbers, in order. */
export function splitCitations(answer: string): Segment[] {
  const segments: Segment[] = [];
  let last = 0;
  for (const match of answer.matchAll(MARKER)) {
    if (match.index > last) segments.push({ text: answer.slice(last, match.index) });
    for (const n of match[1].split(/\s*[,;]\s*/)) segments.push({ cite: Number(n) });
    last = match.index + match[0].length;
  }
  if (last < answer.length) segments.push({ text: answer.slice(last) });
  return segments;
}
