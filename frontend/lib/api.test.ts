import { describe, expect, it, vi } from "vitest";

import { saveSessionSnapshot, sendMessage } from "./api";

/**
 * Regression: the SSE parser must accept CRLF.
 *
 * sse-starlette separates lines with `\r\n` and blocks with `\r\n\r\n`. The
 * parser originally looked only for `"\n\n"`, so it received nothing: the page
 * looked like clicking did nothing while the backend logged a clean 200 - the
 * hardest kind of failure to chase. curl plus Python's splitlines() hides the
 * bug, which is precisely why it has to be tested at this layer.
 */

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

async function collect(chunks: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => streamOf(chunks)),
  );
  const out = [];
  for await (const ev of sendMessage("sid", "hello")) out.push(ev);
  vi.unstubAllGlobals();
  return out;
}

describe("sendMessage SSE parsing", () => {
  it("parses CRLF-separated events, as sse-starlette actually emits them", async () => {
    const events = await collect([
      'event: stage\r\ndata: {"node": "task_compiler"}\r\n\r\n',
      'event: done\r\ndata: {"ready_for_planning": true}\r\n\r\n',
    ]);

    expect(events).toEqual([
      { event: "stage", data: { node: "task_compiler" } },
      { event: "done", data: { ready_for_planning: true } },
    ]);
  });

  it("also accepts LF separation, as other SSE servers emit", async () => {
    const events = await collect(['event: stage\ndata: {"node": "x"}\n\n']);
    expect(events).toEqual([{ event: "stage", data: { node: "x" } }]);
  });

  it("reassembles an event split across network chunks", async () => {
    const events = await collect(["event: sta", 'ge\r\ndata: {"node"', ': "x"}\r\n\r\n']);
    expect(events).toEqual([{ event: "stage", data: { node: "x" } }]);
  });

  it("does not drop a final block with no trailing blank line", async () => {
    const events = await collect(['event: done\r\ndata: {"ok": true}']);
    expect(events).toEqual([{ event: "done", data: { ok: true } }]);
  });

  it("ignores comment-only keepalive blocks", async () => {
    const events = await collect([": ping\r\n\r\n", 'event: done\r\ndata: {"ok": true}\r\n\r\n']);
    expect(events).toEqual([{ event: "done", data: { ok: true } }]);
  });

  it("returns non-JSON data as a string instead of throwing", async () => {
    const events = await collect(["event: note\r\ndata: plain text\r\n\r\n"]);
    expect(events).toEqual([{ event: "note", data: "plain text" }]);
  });
});

describe("session archive", () => {
  it("updates a workspace snapshot with PUT", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await saveSessionSnapshot("sid", {
      status: "idle",
      messages: [],
      contract: null,
      stages: {
        requirement_understanding: "pending",
        task_compiler: "pending",
        ambiguity_resolution: "pending",
        analysis_contract: "pending",
        planning: "pending",
        execution: "pending",
      },
      plan: null,
      layers: [],
      rasters: [],
      fireDataStatus: null,
      fireLifecycle: null,
      spatialAnalysis: null,
      fireContext: null,
      analysisView: "difference",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/sid/snapshot",
      expect.objectContaining({ method: "PUT" }),
    );
    vi.unstubAllGlobals();
  });
});
