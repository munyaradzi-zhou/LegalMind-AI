// The API's verdict on every answer: is it backed by the sources it cites?

export type Grounding = "documents" | "not_grounded" | "unverified" | "no_documents" | "blocked";

export const GROUNDING: Record<Grounding, { label: string; note: string; tone: "ok" | "warn" | "info" }> = {
  documents: {
    label: "Checked against the sources",
    note: "Every statement was found in the cited sources.",
    tone: "ok",
  },
  not_grounded: {
    label: "Not fully supported",
    note: "Some statements could not be found in the sources. Check them before relying on this answer.",
    tone: "warn",
  },
  unverified: {
    label: "Not checked",
    note: "This answer could not be checked against your documents.",
    tone: "warn",
  },
  no_documents: {
    label: "No sources found",
    note: "No matching passages were found in your documents; this answer is based on general knowledge.",
    tone: "info",
  },
  blocked: {
    label: "Blocked",
    note: "The safety checks stopped this request.",
    tone: "warn",
  },
};

export function groundingOf(value: string | null | undefined): (typeof GROUNDING)[Grounding] | null {
  return value && value in GROUNDING ? GROUNDING[value as Grounding] : null;
}

// Progress events from the API (status: {"step": ...}) as the user sees them
export const STEPS: Record<string, string> = {
  checking_question: "Checking your question…",
  searching: "Searching the documents…",
  answering: "Writing the answer…",
  checking_answer: "Checking the answer against the sources…",
};
