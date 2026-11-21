import { describe, expect, it } from "vitest";

import { parseEvents, readEvents } from "./sse";

describe("parseEvents", () => {
  it("parses complete events and keeps the unfinished rest", () => {
    const { events, rest } = parseEvents('event: status\ndata: {"step":"searching"}\n\ndata: {"content":"Huur');
    expect(events).toEqual([{ event: "status", data: '{"step":"searching"}' }]);
    expect(rest).toBe('data: {"content":"Huur');
  });

  it("defaults to the message event, joins multi-line data and skips pings", () => {
    const { events } = parseEvents(": ping\n\ndata: one\ndata: two\n\n");
    expect(events).toEqual([{ event: "message", data: "one\ntwo" }]);
  });

  it("accepts CRLF line endings (sse-starlette sends them)", () => {
    const { events } = parseEvents('event: message\r\ndata: {"done":true}\r\n\r\n');
    expect(events).toEqual([{ event: "message", data: '{"done":true}' }]);
  });
});

describe("readEvents", () => {
  it("reassembles events split across network chunks", async () => {
    const chunks = ["event: sta", 'tus\ndata: {"step":"answering"}\n', "\ndata: {\"content\":\"Ja\"}\n\n"];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
        controller.close();
      },
    });
    const seen = [];
    for await (const event of readEvents(body)) seen.push(event);
    expect(seen).toEqual([
      { event: "status", data: '{"step":"answering"}' },
      { event: "message", data: '{"content":"Ja"}' },
    ]);
  });
});
