// The batch-2 half of `lib/api.ts`.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, parseSseBuffer, setToken } from "@/lib/api";

function streamingReply(chunks: string[], status = 200) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
  return { ok: status >= 200 && status < 300, status, body, headers: new Headers() } as unknown as Response;
}

function jsonReply(status: number, body: unknown = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: new Headers(),
  } as unknown as Response;
}

const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

beforeEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  setToken("a-token");
});


describe("parseSseBuffer", () => {
  it("splits complete events and keeps the unfinished rest", () => {
    const { events, rest } = parseSseBuffer(
      'event: token\ndata: {"text":"Hi"}\n\nevent: token\ndata: {"te'
    );
    expect(events).toEqual([{ event: "token", data: '{"text":"Hi"}' }]);
    expect(rest).toBe('event: token\ndata: {"te');
  });

  it("copes with CRLF line endings", () => {
    const { events } = parseSseBuffer('event: done\r\ndata: {}\r\n\r\n');
    expect(events).toEqual([{ event: "done", data: "{}" }]);
  });

  it("defaults the event name to message", () => {
    expect(parseSseBuffer("data: x\n\n").events).toEqual([{ event: "message", data: "x" }]);
  });

  it("ignores a frame with no data", () => {
    expect(parseSseBuffer(": keep-alive comment\n\n").events).toEqual([]);
  });
});


describe("streamQuestion", () => {
  const done = { assistant_message: { id: "a1", content: "Hello world" }, citations: [], model: "m" };

  it("passes a reset on, so a discarded draft can be cleared", async () => {
    const body = [
      sse("token", { text: "Let me check. " }),
      sse("reset", {}),
      sse("token", { text: "Hello world" }),
      sse("done", done),
    ].join("");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamingReply([body])));
    const calls: string[] = [];
    await api.streamQuestion("s1", "hi", { mode: "agent" }, {
      onToken: (t) => calls.push(`token:${t}`),
      onReset: () => calls.push("reset"),
    });
    expect(calls).toEqual(["token:Let me check. ", "reset", "token:Hello world"]);
  });

  it("calls each handler in order and returns the saved answer", async () => {
    const body = [
      sse("start", { session_id: "s1", user_message_id: "u1", mode: "rag", title: "Hi" }),
      sse("query", { search_query: "hi", rewritten: false }),
      sse("citations", { citations: [{ number: 1 }] }),
      sse("token", { text: "Hello " }),
      sse("token", { text: "world" }),
      sse("done", done),
    ].join("");
    const fetchMock = vi.fn().mockResolvedValue(streamingReply([body.slice(0, 37), body.slice(37)]));
    vi.stubGlobal("fetch", fetchMock);
    const calls: string[] = [];

    const result = await api.streamQuestion("s1", "hi", { mode: "rag" }, {
      onStart: () => calls.push("start"),
      onQuery: (q) => calls.push(`query:${q.search_query}`),
      onCitations: (c) => calls.push(`citations:${c.length}`),
      onToken: (t) => calls.push(`token:${t}`),
    });

    expect(calls).toEqual(["start", "query:hi", "citations:1", "token:Hello ", "token:world"]);
    expect(result).toEqual(done);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/chat/sessions/s1/messages/stream");
    expect(options.headers.Authorization).toBe("Bearer a-token");
    expect(JSON.parse(options.body)).toEqual({ message: "hi", mode: "rag" });
  });

  it("delivers agent steps as they arrive", async () => {
    const steps: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        streamingReply([
          sse("step", { tool: "list_documents", args: {}, summary: "Listed 2" }),
          sse("step", { tool: "search_documents", args: { query: "x" }, summary: "Found 3" }),
          sse("done", done),
        ])
      )
    );

    await api.streamQuestion("s1", "q", { mode: "agent" }, { onStep: (s) => steps.push(s.tool) });

    expect(steps).toEqual(["list_documents", "search_documents"]);
  });

  it("sends the document filter only when one is set", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => streamingReply([sse("done", done)]));
    vi.stubGlobal("fetch", fetchMock);

    await api.streamQuestion("s1", "q", { documentIds: ["d1", "d2"] });
    await api.streamQuestion("s1", "q", { documentIds: [] });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ message: "q", document_ids: ["d1", "d2"] });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ message: "q" });
  });

  it("turns an error event into a thrown error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        streamingReply([sse("token", { text: "Par" }), sse("error", { detail: "Could not get an answer." })])
      )
    );

    await expect(api.streamQuestion("s1", "q")).rejects.toThrow("Could not get an answer.");
  });

  it("reports a stream that ends without finishing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamingReply([sse("token", { text: "Par" })])));

    await expect(api.streamQuestion("s1", "q")).rejects.toThrow(/interrupted/);
  });

  it("a refusal before the stream keeps its status (e.g. the quota's 429)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonReply(429, { detail: "Monthly token quota reached." }))
    );

    const error = await api.streamQuestion("s1", "q").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(429);
    expect((error as ApiError).message).toBe("Monthly token quota reached.");
  });
});


describe("project and document management", () => {
  it.each([
    ["getProject", () => api.getProject("p1"), "/projects/p1", undefined, undefined],
    ["renameProject", () => api.renameProject("p1", "New"), "/projects/p1", "PATCH", { name: "New" }],
    ["deleteProject", () => api.deleteProject("p1"), "/projects/p1", "DELETE", undefined],
    ["renameDocument", () => api.renameDocument("d1", "x.pdf"), "/documents/d1", "PATCH", { filename: "x.pdf" }],
    ["deleteDocument", () => api.deleteDocument("d1"), "/documents/d1", "DELETE", undefined],
    ["reindexDocument", () => api.reindexDocument("d1", { chunk_size: 300 }), "/documents/d1/reindex", "POST", { chunk_size: 300 }],
    ["renameChatSession", () => api.renameChatSession("s1", "Leave"), "/chat/sessions/s1", "PATCH", { title: "Leave" }],
    ["listProjectChatSessions", () => api.listProjectChatSessions("p1"), "/chat/sessions?project_id=p1", undefined, undefined],
    ["getFeedbackSummary", () => api.getFeedbackSummary(7), "/org/feedback?days=7", undefined, undefined],
  ])("%s", async (_name, call, path, method, body) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonReply(200, {}));
    vi.stubGlobal("fetch", fetchMock);

    await call();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toMatch(new RegExp(`/api/v1${path.replace(/[?]/g, "\\?")}$`));
    expect(options.method).toBe(method);
    if (body) expect(JSON.parse(options.body)).toEqual(body);
  });

  it("documentChunks asks for the passage and its neighbours", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonReply(200, []));
    vi.stubGlobal("fetch", fetchMock);

    await api.documentChunks("d1", "c9", 1);

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/documents\/d1\/chunks\?window=1&around=c9$/);
  });

  it("search sends the document filter only when used", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonReply(200, { results: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.search("q", "p1", 10, ["d1"]);
    await api.search("q", "p1", 10);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body).document_ids).toEqual(["d1"]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).not.toHaveProperty("document_ids");
  });

  it("fetchDocumentFile returns the bytes, authenticated", async () => {
    const bytes = new Uint8Array([37, 80, 68, 70]).buffer;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => bytes,
      headers: new Headers(),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.fetchDocumentFile("d1");

    expect(result).toBe(bytes);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/documents\/d1\/preview$/);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer a-token");
  });

  it("fetchDocumentFile surfaces the backend's reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonReply(410, { detail: "This file was quarantined." }))
    );
    await expect(api.fetchDocumentFile("d1")).rejects.toThrow("This file was quarantined.");
  });
});


describe("uploadFile", () => {
  class FakeXhr {
    static last: FakeXhr;
    upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = {
      onprogress: null,
    };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    status = 0;
    responseText = "";
    headers: Record<string, string> = {};
    method = "";
    url = "";
    body: FormData | null = null;
    constructor() {
      FakeXhr.last = this;
    }
    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }
    getResponseHeader() {
      return null;
    }
    send(body: FormData) {
      this.body = body;
    }
  }

  it("reports progress and returns the created documents", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const progress: number[] = [];

    const pending = api.uploadFile("p1", new File(["x"], "a.pdf"), (f) => progress.push(f));
    const xhr = FakeXhr.last;
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
    xhr.status = 202;
    xhr.responseText = JSON.stringify([{ id: "d1", filename: "a.pdf" }]);
    xhr.onload?.();

    await expect(pending).resolves.toEqual([{ id: "d1", filename: "a.pdf" }]);
    expect(progress).toEqual([0.5, 1]);
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toMatch(/\/documents\/upload$/);
    expect(xhr.headers.Authorization).toBe("Bearer a-token");
    expect((xhr.body as FormData).get("project_id")).toBe("p1");
  });

  it("turns a refusal into an ApiError with its status", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);

    const pending = api.uploadFile("p1", new File(["x"], "a.exe"));
    const xhr = FakeXhr.last;
    xhr.status = 415;
    xhr.responseText = JSON.stringify({ detail: "a.exe: this file type is not supported." });
    xhr.onload?.();

    const error = await pending.catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(415);
  });
});

describe("collaboration calls", () => {
  it("hit the right endpoints", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonReply(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    await api.createShareLink("message", "m1", 7);
    await api.listShareLinks("session", "s1");
    await api.revokeShareLink("x1");
    await api.createPrompt("T", "B", "org");
    await api.compareDocuments("a", "b", "  fees ");
    const sent = fetchMock.mock.calls.map(([url, options]) => [
      String(url).replace(/^.*\/api\/v1/, ""), options?.method ?? "GET", options?.body ? JSON.parse(options.body) : null,
    ]);
    expect(sent).toEqual([
      ["/shares", "POST", { kind: "message", target_id: "m1", expires_in_days: 7 }],
      ["/shares?kind=session&target_id=s1", "GET", null],
      ["/shares/x1", "DELETE", null],
      ["/prompts", "POST", { title: "T", body: "B", scope: "org" }],
      ["/compare", "POST", { document_a: "a", document_b: "b", focus: "fees" }],
    ]);
  });

  it("reads a public share WITHOUT the signed-in user's token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonReply(200, { kind: "message", title: "t" }));
    vi.stubGlobal("fetch", fetchMock);
    await api.getPublicShare("dms_abc");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/public/shares/dms_abc");
    expect(options?.headers?.Authorization).toBeUndefined();
  });
});
