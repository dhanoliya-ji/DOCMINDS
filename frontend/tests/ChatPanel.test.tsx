/**
 * ChatPanel.test.tsx
 * ==================
 * WHAT THIS FILE TESTS
 * --------------------
 * `components/ChatPanel.tsx` -- the component the whole project builds
 * towards. Questions in, streamed answers with citations out, and a history
 * of conversations to come back to.
 *
 * THE ONES THAT MATTER MOST
 * -------------------------
 * 1. THE OPTIMISTIC ROLLBACK. The question (and an empty answer bubble) are
 *    added before anything arrives, so the wait is legible. If the request
 *    then fails -- or is stopped -- BOTH must be removed again. An optimistic
 *    update without its rollback lies to the user the first time a request
 *    fails.
 *
 * 2. STREAMING. Words and research steps must appear AS THEY ARRIVE, not all
 *    at once at the end -- that is the whole point of the streaming endpoint.
 *
 * 3. CITATIONS. The numbered sources under an answer are what make a claim
 *    checkable, and "View" must open the right document at the right place.
 *
 * 4. HISTORY. A conversation is created on its first question (never on
 *    mount, which would litter the sidebar), and can be resumed, renamed and
 *    deleted.
 *
 * `lib/api` and `react-markdown` are mocked -- what is under test is this
 * component's behaviour. The stream is simulated by a fake `streamQuestion`
 * that calls the handlers the way the real SSE reader does.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ChatPanel from "@/components/ChatPanel";

const listProjectChatSessions = vi.fn();
const createChatSession = vi.fn();
const streamQuestion = vi.fn();
const listMessages = vi.fn();
const renameChatSession = vi.fn();
const deleteChatSession = vi.fn();
const sendFeedback = vi.fn();
const documentChunks = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listProjectChatSessions: (...a: unknown[]) => listProjectChatSessions(...a),
    createChatSession: (...a: unknown[]) => createChatSession(...a),
    streamQuestion: (...a: unknown[]) => streamQuestion(...a),
    listMessages: (...a: unknown[]) => listMessages(...a),
    renameChatSession: (...a: unknown[]) => renameChatSession(...a),
    deleteChatSession: (...a: unknown[]) => deleteChatSession(...a),
    sendFeedback: (...a: unknown[]) => sendFeedback(...a),
    documentChunks: (...a: unknown[]) => documentChunks(...a),
  },
  getErrorMessage: (error: unknown, fallback = "Something went wrong.") =>
    error instanceof Error && error.message ? error.message : fallback,
}));

// Downloads and the clipboard are browser services jsdom lacks; the
// Markdown they would receive is checked through these spies instead.
const downloadText = vi.fn();
const copyText = vi.fn();
vi.mock("@/lib/export", async () => {
  const actual = await vi.importActual<typeof import("@/lib/export")>("@/lib/export");
  return {
    ...actual,
    downloadText: (...a: unknown[]) => downloadText(...a),
    copyText: (...a: unknown[]) => copyText(...a),
  };
});

// react-markdown is ESM-only and renders prose we are not testing. A plain
// passthrough keeps the answer text queryable.
vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

function citation(n: number, overrides: Record<string, unknown> = {}) {
  return {
    number: n,
    chunk_id: `chunk-${n}`,
    document_id: `doc-${n}`,
    filename: "handbook.docx",
    page_number: 12,
    score: 0.83,
    snippet: `Snippet of source number ${n}.`,
    ...overrides,
  };
}

function session(id: string, title: string) {
  return {
    id, title, project_id: "p1", user_id: "u1",
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  };
}

/** The shape the stream's final "done" event carries. */
function answer(content: string, citations: unknown[] = [], extra: Record<string, unknown> = {}) {
  return {
    session_id: "session-1",
    user_message: {
      id: "user-1", session_id: "session-1", role: "user",
      content: "the question", citations: [], created_at: new Date().toISOString(),
    },
    assistant_message: {
      id: "assistant-1", session_id: "session-1", role: "assistant",
      content, citations: [], created_at: new Date().toISOString(),
    },
    citations,
    model: "test-model",
    mode: "rag",
    steps: [],
    ...extra,
  };
}

type Handlers = {
  onStart?: (e: unknown) => void;
  onStep?: (s: unknown) => void;
  onCitations?: (c: unknown[]) => void;
  onToken?: (t: string) => void;
};

/** A streamQuestion fake: sources, then the answer word by word, then done. */
function streams(content: string, citations: unknown[] = [], extra: Record<string, unknown> = {}) {
  return async (_id: string, _q: string, _options: unknown, handlers: Handlers) => {
    handlers.onCitations?.(citations);
    for (const piece of content.split(/(?<= )/)) handlers.onToken?.(piece);
    return answer(content, citations, extra);
  };
}

/** A stream the TEST drives step by step, for checking what shows mid-answer. */
function controlledStream() {
  const control: {
    handlers: Handlers;
    finish: (value: unknown) => void;
    fail: (error: unknown) => void;
    options: { signal?: AbortSignal };
  } = { handlers: {}, finish: () => {}, fail: () => {}, options: {} };
  streamQuestion.mockImplementation(
    (_id: string, _q: string, options: { signal?: AbortSignal }, handlers: Handlers) =>
      new Promise((resolve, reject) => {
        control.handlers = handlers;
        control.options = options;
        control.finish = resolve;
        control.fail = reject;
        options.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      })
  );
  return control;
}

const box = () => screen.getByPlaceholderText(/ask a question/i);

async function ask(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(box(), text);
  await user.keyboard("{Enter}");
}

/** Render, and wait for the history to have loaded. */
async function renderPanel(props: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const view = render(<ChatPanel projectId="p1" {...props} />);
  await waitFor(() => expect(listProjectChatSessions).toHaveBeenCalled());
  return view;
}

beforeEach(() => {
  listProjectChatSessions.mockReset().mockResolvedValue([]);
  createChatSession.mockReset().mockResolvedValue(session("session-1", "New Chat"));
  streamQuestion.mockReset().mockImplementation(streams("Here is the answer."));
  listMessages.mockReset().mockResolvedValue([]);
  renameChatSession.mockReset();
  deleteChatSession.mockReset().mockResolvedValue({});
  sendFeedback.mockReset().mockResolvedValue({});
  documentChunks.mockReset().mockResolvedValue([]);
  downloadText.mockReset();
  copyText.mockReset().mockResolvedValue(true);
});

// ======================================================================
// Conversations: history, creation, resume, rename, delete
// ======================================================================

describe("conversation history", () => {
  it("lists this project's conversations", async () => {
    listProjectChatSessions.mockResolvedValue([session("s1", "Leave policy"), session("s2", "Expenses")]);
    await renderPanel();

    expect(listProjectChatSessions).toHaveBeenCalledWith("p1");
    expect(await screen.findByText("Leave policy")).toBeInTheDocument();
    expect(screen.getByText("Expenses")).toBeInTheDocument();
  });

  it("does NOT create a conversation just by opening the tab", async () => {
    // Creating one on mount would fill the sidebar with empty "New Chat"s.
    await renderPanel();
    await new Promise((r) => setTimeout(r, 20));
    expect(createChatSession).not.toHaveBeenCalled();
  });

  it("creates the conversation, scoped to the project, on the first question", async () => {
    const user = userEvent.setup();
    await renderPanel({ projectId: "project-42" });

    await ask(user, "How much leave?");

    await waitFor(() => expect(createChatSession).toHaveBeenCalledWith("project-42"));
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    expect(streamQuestion.mock.calls[0][0]).toBe("session-1");
  });

  it("reuses the conversation for the next question", async () => {
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "First?");
    await screen.findByText("Here is the answer.");
    await ask(user, "Second?");

    await waitFor(() => expect(streamQuestion).toHaveBeenCalledTimes(2));
    expect(createChatSession).toHaveBeenCalledTimes(1);
    expect(streamQuestion.mock.calls[1][0]).toBe("session-1");
  });

  it("shows an error if the conversation cannot be created", async () => {
    createChatSession.mockRejectedValue(new Error("Could not reach the server."));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    expect(await screen.findByText(/could not reach the server/i)).toBeInTheDocument();
    expect(screen.queryByText("A question")).not.toBeInTheDocument();
  });

  it("resumes a conversation with its saved answers, mode and research steps", async () => {
    listProjectChatSessions.mockResolvedValue([session("s1", "Leave policy")]);
    listMessages.mockResolvedValue([
      { id: "m1", session_id: "s1", role: "user", content: "How much leave?", citations: [],
        tokens_used: null, latency: null, created_at: "2026-10-01T00:00:00Z" },
      { id: "m2", session_id: "s1", role: "assistant", content: "25 days [1].",
        citations: [citation(1)], tokens_used: 40, latency: 1.2, mode: "agent", model: "gpt-oss",
        steps: [{ tool: "search_documents", args: { query: "leave" }, summary: "Found 1 excerpt" }],
        created_at: "2026-10-01T00:00:01Z" },
    ]);
    const user = userEvent.setup();
    await renderPanel();

    await user.click(await screen.findByText("Leave policy"));

    expect(listMessages).toHaveBeenCalledWith("s1");
    expect(await screen.findByText("25 days [1].")).toBeInTheDocument();
    expect(screen.getByText("Deep research", { selector: ".pill-info" })).toBeInTheDocument();
    expect(screen.getByText(/research steps \(1\)/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Leave policy" })).toBeInTheDocument();
  });

  it("asks a follow-up in the resumed conversation", async () => {
    listProjectChatSessions.mockResolvedValue([session("s1", "Leave policy")]);
    const user = userEvent.setup();
    await renderPanel();

    await user.click(await screen.findByText("Leave policy"));
    await ask(user, "And part-timers?");

    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    expect(streamQuestion.mock.calls[0][0]).toBe("s1");
    expect(createChatSession).not.toHaveBeenCalled();
  });

  it("renames a conversation", async () => {
    listProjectChatSessions.mockResolvedValue([session("s1", "Leave policy")]);
    renameChatSession.mockResolvedValue(session("s1", "Annual leave"));
    const user = userEvent.setup();
    await renderPanel();

    await user.click(await screen.findByRole("button", { name: "Rename Leave policy" }));
    const input = screen.getByRole("textbox", { name: /conversation name/i });
    await user.clear(input);
    await user.type(input, "Annual leave{Enter}");

    await waitFor(() => expect(renameChatSession).toHaveBeenCalledWith("s1", "Annual leave"));
    expect(await screen.findByText("Annual leave")).toBeInTheDocument();
  });

  it("deletes a conversation only after confirming", async () => {
    listProjectChatSessions.mockResolvedValue([session("s1", "Leave policy")]);
    const user = userEvent.setup();
    await renderPanel();

    await user.click(await screen.findByRole("button", { name: "Delete Leave policy" }));
    expect(deleteChatSession).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete?" }));

    await waitFor(() => expect(deleteChatSession).toHaveBeenCalledWith("s1"));
    await waitFor(() => expect(screen.queryByText("Leave policy")).not.toBeInTheDocument());
  });

  it("starts a fresh conversation from New chat", async () => {
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "First?");
    await screen.findByText("Here is the answer.");

    await user.click(screen.getByRole("button", { name: "New chat" }));

    expect(screen.queryByText("Here is the answer.")).not.toBeInTheDocument();
    await ask(user, "Fresh question");
    await waitFor(() => expect(createChatSession).toHaveBeenCalledTimes(2));
  });
});

// ======================================================================
// Asking, and the stream
// ======================================================================

describe("asking a question", () => {
  it("sends the question, the mode and no document filter by default", async () => {
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "How much annual leave do I get?");

    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    const [id, question, options] = streamQuestion.mock.calls[0];
    expect(id).toBe("session-1");
    expect(question).toBe("How much annual leave do I get?");
    expect(options).toMatchObject({ mode: "rag", documentIds: [] });
  });

  it("shows the question immediately, before anything arrives", async () => {
    controlledStream();
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A distinctive question");

    expect(await screen.findByText("A distinctive question")).toBeInTheDocument();
  });

  it("writes the answer in as the words arrive", async () => {
    const stream = controlledStream();
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "A question");
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());

    act(() => stream.handlers.onToken?.("Leave is "));
    expect(await screen.findByText("Leave is")).toBeInTheDocument();
    act(() => stream.handlers.onToken?.("25 days."));
    expect(await screen.findByText("Leave is 25 days.")).toBeInTheDocument();

    await act(async () => stream.finish(answer("Leave is 25 days.")));
    expect(await screen.findByText("test-model")).toBeInTheDocument();
    expect(screen.getByText("Leave is 25 days.")).toBeInTheDocument();
  });

  it("clears the input after sending", async () => {
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    await waitFor(() => expect(box()).toHaveValue(""));
  });

  it("does not send an empty question", async () => {
    const user = userEvent.setup();
    await renderPanel();

    await user.click(box());
    await user.keyboard("{Enter}");

    expect(streamQuestion).not.toHaveBeenCalled();
  });

  it("does not send whitespace alone", async () => {
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "    ");

    expect(streamQuestion).not.toHaveBeenCalled();
  });

  it("ignores a second Enter while a request is in flight", async () => {
    controlledStream();
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(streamQuestion).toHaveBeenCalledTimes(1));
  });

  it("disables the input while answering and offers Stop", async () => {
    controlledStream();
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    await waitFor(() => expect(box()).toBeDisabled());
    expect(screen.getByRole("button", { name: /stop answering/i })).toBeInTheDocument();
  });

  it("Stop cancels the answer and says it was not saved", async () => {
    const stream = controlledStream();
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "A question to stop");
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: /stop answering/i }));

    expect(stream.options.signal?.aborted).toBe(true);
    expect(await screen.findByText(/stopped/i)).toBeInTheDocument();
    expect(screen.queryByText("A question to stop")).not.toBeInTheDocument();
    expect(box()).not.toBeDisabled();
  });
});

// ======================================================================
// THE ROLLBACK
// ======================================================================

describe("when the answer fails", () => {
  it("removes the optimistic question and the half-written answer", async () => {
    const stream = controlledStream();
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "A question that will fail");
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    act(() => stream.handlers.onToken?.("Half an ans"));
    await screen.findByText("Half an ans");

    await act(async () => stream.fail(new Error("The model timed out.")));

    await waitFor(() =>
      expect(screen.queryByText("A question that will fail")).not.toBeInTheDocument()
    );
    expect(screen.queryByText("Half an ans")).not.toBeInTheDocument();
  });

  it("shows the reason", async () => {
    streamQuestion.mockRejectedValue(new Error("The model timed out."));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    expect(await screen.findByText(/model timed out/i)).toBeInTheDocument();
  });

  it("re-enables the input so the user can retry", async () => {
    streamQuestion.mockRejectedValue(new Error("Failed."));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    await waitFor(() => expect(box()).not.toBeDisabled());
  });
});

// ======================================================================
// Citations
// ======================================================================

describe("citations", () => {
  it("shows how many sources an answer has", async () => {
    streamQuestion.mockImplementation(streams("The answer.", [citation(1), citation(2), citation(3)]));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    expect(await screen.findByText(/3 sources/i)).toBeInTheDocument();
  });

  it("uses the singular for one source", async () => {
    streamQuestion.mockImplementation(streams("The answer.", [citation(1)]));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    const toggle = await screen.findByText(/1 source/i);
    expect(toggle.textContent).not.toMatch(/sources/i);
  });

  it("offers no source toggle when there are none", async () => {
    streamQuestion.mockImplementation(streams("I don't know.", []));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "Something unanswerable");

    await screen.findByText(/i don't know/i);
    expect(screen.queryByText(/\d+ sources?/i)).not.toBeInTheDocument();
  });

  it("keeps sources collapsed until asked for, then reveals file and page", async () => {
    streamQuestion.mockImplementation(streams("The answer.", [citation(1)]));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await screen.findByText(/1 source/i);
    expect(screen.queryByText(/Snippet of source number 1/)).not.toBeInTheDocument();

    await user.click(await screen.findByText(/1 source/i));

    expect(await screen.findByText(/Snippet of source number 1/)).toBeInTheDocument();
    expect(screen.getByText(/\[1\] handbook\.docx · page 12/)).toBeInTheDocument();
  });

  it("collapses again on a second click", async () => {
    streamQuestion.mockImplementation(streams("The answer.", [citation(1)]));
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await user.click(await screen.findByText(/1 source/i));
    await screen.findByText(/Snippet of source number 1/);
    await user.click(await screen.findByText(/1 source/i));

    await waitFor(() =>
      expect(screen.queryByText(/Snippet of source number 1/)).not.toBeInTheDocument()
    );
  });

  it("numbers the sources to match the answer's markers", async () => {
    streamQuestion.mockImplementation(
      streams("Supported by [1] and [2].", [
        citation(1, { snippet: "First cited passage." }),
        citation(2, { snippet: "Second cited passage." }),
      ])
    );
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await user.click(await screen.findByText(/2 sources/i));

    const first = await screen.findByText(/First cited passage/);
    const second = screen.getByText(/Second cited passage/);
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("handles a source with no page number", async () => {
    streamQuestion.mockImplementation(
      streams("The answer.", [citation(1, { filename: "data.csv", page_number: null })])
    );
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await user.click(await screen.findByText(/1 source/i));

    expect(await screen.findByText(/data\.csv/)).toBeInTheDocument();
    expect(screen.queryByText(/page null/i)).not.toBeInTheDocument();
  });

  it("opens the cited passage in the source viewer", async () => {
    streamQuestion.mockImplementation(streams("The answer.", [citation(1)]));
    documentChunks.mockResolvedValue([
      { id: "chunk-0", chunk_index: 0, page_number: 11, content: "Before." },
      { id: "chunk-1", chunk_index: 1, page_number: 12, content: "Snippet of source number 1. And more." },
    ]);
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await user.click(await screen.findByText(/1 source/i));
    await user.click(await screen.findByRole("button", { name: "View source 1" }));

    const dialog = await screen.findByRole("dialog", { name: /handbook\.docx/ });
    expect(documentChunks).toHaveBeenCalledWith("doc-1", "chunk-1", 2);
    const mark = await within(dialog).findByText("Snippet of source number 1.");
    expect(mark.tagName).toBe("MARK");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

// ======================================================================
// Rating, copying, exporting
// ======================================================================

describe("rating, copying and exporting", () => {
  /**
   * The thumbs buttons carry only an icon. Located through the lucide SVG
   * class; THROWS rather than returning nothing, so a markup change cannot
   * turn these tests into silent passes.
   */
  function thumbs(): HTMLElement[] {
    const found = screen
      .getAllByRole("button")
      .filter((b) => b.querySelector("svg.lucide-thumbs-up, svg.lucide-thumbs-down"));
    if (found.length === 0) throw new Error("no feedback buttons found -- has the markup changed?");
    return found;
  }

  it("sends a positive rating as 1 and a negative as -1, for the shown message", async () => {
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "A question");
    await screen.findByText("Here is the answer.");

    await user.click(thumbs()[0]);
    await user.click(thumbs()[1]);

    await waitFor(() => expect(sendFeedback).toHaveBeenCalledTimes(2));
    expect(sendFeedback.mock.calls[0]).toEqual(["assistant-1", 1]);
    expect(sendFeedback.mock.calls[1]).toEqual(["assistant-1", -1]);
  });

  it("a failed rating never interrupts the conversation", async () => {
    sendFeedback.mockRejectedValue(new Error("Feedback endpoint down"));
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "A question");
    const answerText = await screen.findByText("Here is the answer.");

    await user.click(thumbs()[0]);
    await waitFor(() => expect(sendFeedback).toHaveBeenCalled());

    expect(answerText).toBeInTheDocument();
    expect(screen.queryByText(/feedback endpoint down/i)).not.toBeInTheDocument();
  });

  it("copies an answer with its sources as Markdown", async () => {
    streamQuestion.mockImplementation(streams("25 days [1].", [citation(1)]));
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "A question");
    await screen.findByText("25 days [1].");

    await user.click(screen.getByRole("button", { name: "Copy answer" }));

    await waitFor(() => expect(copyText).toHaveBeenCalled());
    expect(copyText.mock.calls[0][0]).toContain("25 days [1].");
    expect(copyText.mock.calls[0][0]).toContain("1. handbook.docx, page 12");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("exports the conversation as a Markdown file", async () => {
    const user = userEvent.setup();
    await renderPanel();
    expect(screen.getByRole("button", { name: /export/i })).toBeDisabled();

    await ask(user, "How much leave?");
    await screen.findByText("Here is the answer.");
    await user.click(screen.getByRole("button", { name: /export/i }));

    expect(downloadText).toHaveBeenCalledTimes(1);
    const [filename, markdown] = downloadText.mock.calls[0];
    expect(filename).toMatch(/\.md$/);
    expect(markdown).toContain("## Question");
    expect(markdown).toContain("Here is the answer.");
  });
});

// ======================================================================
// Answering only from selected documents
// ======================================================================

describe("the document filter", () => {
  const docs = [
    { id: "d1", filename: "handbook.pdf", status: "completed" },
    { id: "d2", filename: "expenses.docx", status: "completed" },
  ] as never[];

  it("sends only the ticked documents", async () => {
    const user = userEvent.setup();
    await renderPanel({ documents: docs });

    await user.click(screen.getByRole("button", { name: /all documents/i }));
    await user.click(screen.getByRole("option", { name: "expenses.docx" }));
    expect(screen.getByRole("button", { name: /expenses\.docx/i })).toBeInTheDocument();

    await ask(user, "Travel limits?");

    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    expect(streamQuestion.mock.calls[0][2]).toMatchObject({ documentIds: ["d2"] });
  });
});

// ======================================================================
// Answer mode: quick answer (rag) vs deep research (agent)
// ======================================================================

describe("answer mode", () => {
  const step = (tool: string, summary: string, args: Record<string, unknown> = {}) => ({
    tool,
    args,
    summary,
  });

  it("defaults to quick answer and sends the rag mode", async () => {
    const user = userEvent.setup();
    await renderPanel();

    expect(screen.getByRole("radio", { name: /quick answer/i })).toHaveAttribute("aria-checked", "true");

    await ask(user, "A question");

    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    expect(streamQuestion.mock.calls[0][2]).toMatchObject({ mode: "rag" });
  });

  it("sends the agent mode once deep research is selected", async () => {
    const user = userEvent.setup();
    await renderPanel();

    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    await ask(user, "Compare the two policies");

    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    expect(streamQuestion.mock.calls[0][2]).toMatchObject({ mode: "agent" });
  });

  it("explains the tradeoff of the selected mode, linked to the control", async () => {
    const user = userEvent.setup();
    await renderPanel();

    expect(screen.getByText(/fastest/i)).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    expect(screen.getByText(/slower/i)).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: /answer mode/i })).toHaveAttribute(
      "aria-describedby",
      "chat-mode-hint"
    );
  });

  it("locks the toggle while a question is in flight", async () => {
    controlledStream();
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");

    await waitFor(() => expect(screen.getByRole("radio", { name: /deep research/i })).toBeDisabled());
  });

  it("says it is researching until the first step arrives", async () => {
    controlledStream();
    const user = userEvent.setup();
    await renderPanel();

    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    await ask(user, "A hard question");

    expect(await screen.findByText(/researching your documents/i)).toBeInTheDocument();
  });

  it("does not say it is researching for a quick answer", async () => {
    controlledStream();
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());

    expect(screen.queryByText(/researching your documents/i)).not.toBeInTheDocument();
  });

  it("shows each research step LIVE, while the agent is still working", async () => {
    const stream = controlledStream();
    const user = userEvent.setup();
    await renderPanel();
    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    await ask(user, "Compare the policies");
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());

    act(() => stream.handlers.onStep?.(step("list_documents", "Listed 2 documents")));
    expect(await screen.findByText("Listed 2 documents")).toBeInTheDocument();
    act(() => stream.handlers.onStep?.(step("search_documents", "Found 5 excerpts", { query: "leave" })));
    expect(await screen.findByText("Found 5 excerpts")).toBeInTheDocument();
    expect(screen.getByText(/query: "leave"/)).toBeInTheDocument();
  });

  it("keeps the steps afterwards behind a toggle, in order", async () => {
    streamQuestion.mockImplementation(
      streams("Done.", [], {
        mode: "agent",
        steps: [step("list_documents", "Listed 2 documents"), step("search_documents", "Found 5 excerpts")],
      })
    );
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    const toggle = await screen.findByText(/research steps \(2\)/i);
    expect(screen.queryByText("Listed 2 documents")).not.toBeInTheDocument();

    await user.click(toggle);

    const list = await screen.findByRole("list", { name: /research steps/i });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("list_documents");
    expect(items[1]).toHaveTextContent("search_documents");
  });

  it("shows no steps toggle for a quick answer", async () => {
    const user = userEvent.setup();
    await renderPanel();

    await ask(user, "A question");
    await screen.findByText("Here is the answer.");

    expect(screen.queryByText(/research steps/i)).not.toBeInTheDocument();
  });

  it("is one tab stop, switched with the arrow keys", async () => {
    const user = userEvent.setup();
    await renderPanel();

    const quick = screen.getByRole("radio", { name: /quick answer/i });
    const deep = screen.getByRole("radio", { name: /deep research/i });
    expect(quick).toHaveAttribute("tabindex", "0");
    expect(deep).toHaveAttribute("tabindex", "-1");

    quick.focus();
    await user.keyboard("{ArrowRight}");

    expect(deep).toHaveAttribute("aria-checked", "true");
    expect(deep).toHaveFocus();
  });
});

// ======================================================================
// F10: the live step indicator during deep research
// ======================================================================

const agentStep = (tool: string, summary: string) => ({ tool, summary, args: {} });

describe("live step indicator", () => {
  it("morphs search -> read -> search -> summarise as the stream moves on", async () => {
    const stream = controlledStream();
    const user = userEvent.setup();
    await renderPanel();
    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    await ask(user, "Compare the policies");
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());

    const indicator = () => screen.getByTestId("agent-step-indicator");
    // Before any step: searching, announced politely.
    expect(indicator()).toHaveAttribute("data-phase", "search");
    expect(indicator()).toHaveAttribute("role", "status");
    expect(indicator()).toHaveTextContent("Searching your documents");

    act(() => stream.handlers.onStep?.(agentStep("read_document_section", "Read section 3")));
    await waitFor(() => expect(indicator()).toHaveAttribute("data-phase", "read"));
    expect(indicator()).toHaveTextContent("Reading the relevant sections");

    act(() => stream.handlers.onStep?.(agentStep("search_documents", "Found 5 excerpts")));
    await waitFor(() => expect(indicator()).toHaveAttribute("data-phase", "search"));

    act(() => stream.handlers.onToken?.("The answer"));
    await waitFor(() => expect(indicator()).toHaveAttribute("data-phase", "summarise"));

    await act(async () => stream.finish(answer("The answer", [], { mode: "agent" })));
    await waitFor(() => expect(screen.queryByTestId("agent-step-indicator")).not.toBeInTheDocument());
  });

  it("never appears for a quick answer", async () => {
    controlledStream();
    const user = userEvent.setup();
    await renderPanel();
    await ask(user, "Quick one");
    await waitFor(() => expect(streamQuestion).toHaveBeenCalled());
    expect(screen.queryByTestId("agent-step-indicator")).not.toBeInTheDocument();
  });

  it("is static under prefers-reduced-motion", async () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes("reduce"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    try {
      controlledStream();
      const user = userEvent.setup();
      await renderPanel();
      await user.click(screen.getByRole("radio", { name: /deep research/i }));
      await ask(user, "Compare");
      expect(await screen.findByTestId("agent-step-indicator")).toHaveAttribute("data-reduced-motion", "true");
    } finally {
      window.matchMedia = original;
    }
  });
});

describe("agentPhase", () => {
  it("reads the phase off the steps and the text", async () => {
    const { agentPhase } = await import("@/components/AgentStepIndicator");
    expect(agentPhase([], "")).toBe("search");
    expect(agentPhase([agentStep("list_documents", "x")], "")).toBe("read");
    expect(agentPhase([agentStep("search_documents", "x")], "")).toBe("search");
    expect(agentPhase([agentStep("read_document_section", "x")], "words")).toBe("summarise");
  });
});
