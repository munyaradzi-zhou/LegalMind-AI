// The LegalMind API, called straight from the browser with the user's token.

import type { Citation } from "./citations";
import type { Grounding } from "./grounding";
import { readEvents } from "./sse";

export type Me = {
  user_id: string;
  name: string;
  roles: string[];
  matters: string[] | "all";
  can_ingest: boolean;
  sees_privileged: boolean;
  sign_in_required: boolean;
};

export type Answer = {
  session_id: string;
  agent_name: string;
  citations: Citation[];
  grounding: Grounding;
  groundedness_score: number | null;
};

export type StreamHandlers = {
  onStatus: (step: string) => void;
  onText: (piece: string) => void;
  onDone: (answer: Answer) => void;
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function headers(token: string | null, extra: Record<string, string> = {}): Record<string, string> {
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function failure(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  try {
    const body = await response.json();
    detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
  } catch {
    // not JSON
  }
  return new ApiError(response.status, detail || `HTTP ${response.status}`);
}

export async function getMe(apiUrl: string, token: string | null): Promise<Me> {
  const response = await fetch(`${apiUrl}/api/me`, { headers: headers(token) });
  if (!response.ok) throw await failure(response);
  return response.json();
}

/** Ask a question; progress, the answer's text and finally its citations arrive through ``handlers``. */
export async function streamChat(
  apiUrl: string,
  token: string | null,
  message: string,
  sessionId: string | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${apiUrl}/api/chat`, {
    method: "POST",
    headers: headers(token, { "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify({ message, session_id: sessionId, stream: true }),
    signal,
  });
  if (!response.ok || !response.body) throw await failure(response);
  for await (const { event, data } of readEvents(response.body)) {
    const payload = JSON.parse(data);
    if (event === "status") handlers.onStatus(payload.step);
    else if (event === "error") throw new ApiError(payload.status, payload.detail);
    else if (payload.done) handlers.onDone(payload as Answer);
    else handlers.onText(payload.content);
  }
}

export type IngestResult = { document_id: string; chunk_count: number; page_count: number; entities_found: number };

/** Upload a document (Admin only). XHR rather than fetch: it reports upload progress. */
export function ingest(
  apiUrl: string,
  token: string | null,
  form: FormData,
  onUploaded: (fraction: number) => void,
): Promise<IngestResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${apiUrl}/api/ingest`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onUploaded(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: unknown } & Partial<IngestResult> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // not JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as IngestResult);
      else reject(new ApiError(xhr.status, typeof body.detail === "string" ? body.detail : `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new ApiError(0, "The upload could not reach the API"));
    xhr.send(form);
  });
}
