// Server-Sent Events over fetch: EventSource can't send an Authorization header or a POST
// body, so the chat stream is read from the response body and parsed here.

export type SseEvent = { event: string; data: string };

/** Split a growing text buffer into complete events; returns them and the unfinished rest. */
export function parseEvents(buffer: string): { events: SseEvent[]; rest: string } {
  const normalised = buffer.replace(/\r\n?/g, "\n");
  const blocks = normalised.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: SseEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue; // comment / keep-alive ping
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

/** Read a streamed response body as SSE events, in order. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const { events, rest } = parseEvents(done ? `${buffer}\n\n` : buffer);
    buffer = rest;
    yield* events;
    if (done) return;
  }
}
