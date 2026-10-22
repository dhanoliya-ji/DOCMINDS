/**
 * ChatPanel.test.tsx
 * ==================
 * WHAT THIS FILE TESTS
 * --------------------
 * `components/ChatPanel.tsx` -- the component the whole project builds
 * towards. Questions in, answers with citations out.
 *
 * THE TWO THAT MATTER MOST
 * ------------------------
 * 1. THE OPTIMISTIC ROLLBACK. The question is added to the transcript before
 *    the answer arrives, so the wait is legible. If the request then fails,
 *    that placeholder must be REMOVED again. An optimistic update without its
 *    rollback is the usual half-built version of this pattern, and it lies to
 *    the user the first time a request fails -- leaving a question sitting in
 *    the transcript that was never answered and never will be.
 *
 * 2. CITATIONS. The numbered sources under an answer are what make a claim
 *    checkable. A missing or mismatched citation does not look like a bug; it
 *    looks like an answer.
 *
 * 3. ANSWER MODE. "Quick answer" and "Deep research" are different backend
 *    pipelines. The toggle must send the mode the user actually picked, and
 *    the research steps an agent returns must be shown -- they are the only
 *    record of how a deep-research answer was reached, since the backend does
 *    not save them.
 *
 * `lib/api` and `react-markdown` are both mocked -- what is under test is this
 * component's behaviour, not markdown rendering.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ChatPanel from "@/components/ChatPanel";

const createChatSession = vi.fn();
const askQuestion = vi.fn();
const sendFeedback = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    createChatSession: (...a: unknown[]) => createChatSession(...a),
    askQuestion: (...a: unknown[]) => askQuestion(...a),
    sendFeedback: (...a: unknown[]) => sendFeedback(...a),
  },
  getErrorMessage: (error: unknown, fallback = "Something went wrong.") =>
    error instanceof Error && error.message ? error.message : fallback,
}));

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
    filename: "handbook.pdf",
    page_number: 12,
    score: 0.83,
    snippet: `Snippet of source number ${n}.`,
    ...overrides,
  };
}

/** The shape POST /chat/sessions/{id}/messages returns. */
function answer(content: string, citations: unknown[] = []) {
  return {
    user_message: {
      id: "user-1", session_id: "s1", role: "user",
      content: "the question", citations: [], created_at: new Date().toISOString(),
    },
    assistant_message: {
      id: "assistant-1", session_id: "s1", role: "assistant",
      content, citations: [], created_at: new Date().toISOString(),
    },
    citations,
  };
}

const box = () => screen.getByPlaceholderText(/ask a question/i);

async function ask(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(box(), text);
  await user.keyboard("{Enter}");
}

beforeEach(() => {
  createChatSession.mockReset().mockResolvedValue({ id: "session-1", title: "New Chat" });
  askQuestion.mockReset().mockResolvedValue(answer("Here is the answer."));
  sendFeedback.mockReset().mockResolvedValue({});
});

// ======================================================================
// Starting a conversation
// ======================================================================

describe("session setup", () => {
  it("creates a session scoped to the project", async () => {
    // Scoping is why a conversation in the HR project does not answer from
    // the finance documents.
    render(<ChatPanel projectId="project-42" />);

    await waitFor(() => expect(createChatSession).toHaveBeenCalledWith("project-42"));
  });

  it("shows an error if the session cannot be created", async () => {
    createChatSession.mockRejectedValue(new Error("Could not reach the server."));
    render(<ChatPanel projectId="p1" />);

    expect(await screen.findByText(/could not reach the server/i)).toBeInTheDocument();
  });

  it("does not send a question before a session exists", async () => {
    // Without a session id there is nowhere to post. Sending anyway would
    // produce a confusing 404 rather than simply waiting.
    createChatSession.mockReturnValue(new Promise(() => {})); // never resolves
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);

    await user.type(box(), "a question");
    await user.keyboard("{Enter}");

    expect(askQuestion).not.toHaveBeenCalled();
  });
});

// ======================================================================
// Asking
// ======================================================================

describe("asking a question", () => {
  it("sends it to the session", async () => {
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "How much annual leave do I get?");

    await waitFor(() => expect(askQuestion).toHaveBeenCalled());
    expect(askQuestion.mock.calls[0][0]).toBe("session-1");
    expect(askQuestion.mock.calls[0][1]).toBe("How much annual leave do I get?");
  });

  it("shows the question immediately, before the answer arrives", async () => {
    // The optimistic append. A model can take seconds; a chat that shows
    // nothing until it finishes reads as broken.
    let release: (v: unknown) => void = () => {};
    askQuestion.mockReturnValue(new Promise((r) => { release = r; }));

    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A distinctive question");

    // Visible while the request is still in flight.
    expect(await screen.findByText("A distinctive question")).toBeInTheDocument();

    release(answer("The answer."));
    await screen.findByText("The answer.");
  });

  it("shows the answer", async () => {
    askQuestion.mockResolvedValue(answer("You accrue 1.75 days per month."));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "How much leave?");

    expect(await screen.findByText(/1\.75 days per month/)).toBeInTheDocument();
  });

  it("clears the input after sending", async () => {
    // Otherwise the next question is typed onto the end of the last one.
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    await waitFor(() => expect(box()).toHaveValue(""));
  });

  it("does not send an empty question", async () => {
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await user.click(box());
    await user.keyboard("{Enter}");

    expect(askQuestion).not.toHaveBeenCalled();
  });

  it("does not send whitespace alone", async () => {
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "    ");

    expect(askQuestion).not.toHaveBeenCalled();
  });

  it("ignores a second Enter while a request is in flight", async () => {
    // Otherwise a double press asks the same question twice, and the
    // transcript is wrong in a way that is hard to explain afterwards.
    askQuestion.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}");

    expect(askQuestion).toHaveBeenCalledTimes(1);
  });

  it("disables the input while sending", async () => {
    askQuestion.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    await waitFor(() => expect(box()).toBeDisabled());
  });
});

// ======================================================================
// THE ROLLBACK
// ======================================================================

describe("when the answer fails", () => {
  it("removes the optimistic question from the transcript", async () => {
    // The half of the optimistic pattern that is usually missing. Leaving the
    // question behind shows the user something that never happened.
    askQuestion.mockRejectedValue(new Error("The model timed out."));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question that will fail");

    await waitFor(() =>
      expect(screen.queryByText("A question that will fail")).not.toBeInTheDocument()
    );
  });

  it("shows the reason", async () => {
    askQuestion.mockRejectedValue(new Error("The model timed out."));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    expect(await screen.findByText(/model timed out/i)).toBeInTheDocument();
  });

  it("re-enables the input so the user can retry", async () => {
    // A failure that leaves the box disabled is a dead end -- the only way out
    // is a page reload.
    askQuestion.mockRejectedValue(new Error("Failed."));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    await waitFor(() => expect(box()).not.toBeDisabled());
  });
});

// ======================================================================
// Citations
// ======================================================================

describe("citations", () => {
  it("shows how many sources an answer has", async () => {
    askQuestion.mockResolvedValue(
      answer("The answer.", [citation(1), citation(2), citation(3)])
    );
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    expect(await screen.findByText(/3 sources/i)).toBeInTheDocument();
  });

  it("uses the singular for one source", async () => {
    askQuestion.mockResolvedValue(answer("The answer.", [citation(1)]));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    const toggle = await screen.findByText(/1 source/i);
    expect(toggle.textContent).not.toMatch(/sources/i);
  });

  it("offers no source toggle when there are none", async () => {
    // An answer with no citations means retrieval found nothing above the
    // score threshold, and the model will have said it does not know. An
    // empty "0 sources" control would invite a pointless click.
    askQuestion.mockResolvedValue(answer("I don't know.", []));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "Something unanswerable");

    await screen.findByText(/i don't know/i);
    expect(screen.queryByText(/source/i)).not.toBeInTheDocument();
  });

  it("keeps sources collapsed until asked for", async () => {
    askQuestion.mockResolvedValue(answer("The answer.", [citation(1)]));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await screen.findByText(/1 source/i);

    expect(screen.queryByText(/Snippet of source number 1/)).not.toBeInTheDocument();
  });

  it("reveals the source, its file and its page when expanded", async () => {
    // This is the whole point of the feature: a claim traced back to a passage
    // the reader can check.
    askQuestion.mockResolvedValue(answer("The answer.", [citation(1)]));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await user.click(await screen.findByText(/1 source/i));

    expect(await screen.findByText(/Snippet of source number 1/)).toBeInTheDocument();
    expect(screen.getByText(/handbook\.pdf/)).toBeInTheDocument();
  });

  it("collapses again on a second click", async () => {
    askQuestion.mockResolvedValue(answer("The answer.", [citation(1)]));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    // Re-queried before each click rather than captured once. Expanding
    // re-renders the message, and React may replace the toggle's DOM node --
    // clicking a stale reference then does nothing, and the test fails for a
    // reason that has nothing to do with the behaviour under test.
    await user.click(await screen.findByText(/1 source/i));
    await screen.findByText(/Snippet of source number 1/);

    await user.click(await screen.findByText(/1 source/i));
    await waitFor(() =>
      expect(screen.queryByText(/Snippet of source number 1/)).not.toBeInTheDocument()
    );
  });

  it("numbers the sources to match the answer's markers", async () => {
    // The model writes "[2]" in its prose, and that number must resolve to the
    // right card. If the numbering drifted, every citation would point at the
    // wrong passage while still looking authoritative.
    askQuestion.mockResolvedValue(
      answer("Supported by [1] and [2].", [
        citation(1, { snippet: "First cited passage." }),
        citation(2, { snippet: "Second cited passage." }),
      ])
    );
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await user.click(await screen.findByText(/2 sources/i));

    const first = await screen.findByText(/First cited passage/);
    const second = screen.getByText(/Second cited passage/);

    // Rendered in citation order, so [1] is above [2].
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("handles a source with no page number", async () => {
    // A CSV or an email has no pages.
    askQuestion.mockResolvedValue(
      answer("The answer.", [citation(1, { filename: "data.csv", page_number: null })])
    );
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await user.click(await screen.findByText(/1 source/i));

    expect(await screen.findByText(/data\.csv/)).toBeInTheDocument();
    expect(screen.queryByText(/page null/i)).not.toBeInTheDocument();
  });
});

// ======================================================================
// Feedback
// ======================================================================

describe("rating an answer", () => {
  /**
   * The thumbs buttons carry only an icon, no text, so there is no accessible
   * name to query by. Located through the lucide SVG class instead.
   *
   * Deliberately THROWS rather than returning an empty list: a test that
   * quietly finds no buttons and passes anyway is worse than no test, because
   * it reports coverage it does not have.
   */
  function thumbs(): HTMLElement[] {
    const found = screen
      .getAllByRole("button")
      .filter((b) => b.querySelector("svg.lucide-thumbs-up, svg.lucide-thumbs-down"));

    if (found.length === 0) {
      throw new Error("no feedback buttons found -- has the markup changed?");
    }
    return found;
  }

  it("offers both a positive and a negative rating", async () => {
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await screen.findByText("Here is the answer.");

    expect(thumbs()).toHaveLength(2);
  });

  it("sends a positive rating as 1", async () => {
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await screen.findByText("Here is the answer.");

    await user.click(thumbs()[0]);

    await waitFor(() => expect(sendFeedback).toHaveBeenCalled());
    expect(sendFeedback.mock.calls[0][1]).toBe(1);
  });

  it("sends a negative rating as -1", async () => {
    // The sign is the entire signal. Sending 1 for both would record every
    // answer as good and quietly destroy the feedback data.
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await screen.findByText("Here is the answer.");

    await user.click(thumbs()[1]);

    await waitFor(() => expect(sendFeedback).toHaveBeenCalled());
    expect(sendFeedback.mock.calls[0][1]).toBe(-1);
  });

  it("rates the message that was actually shown", async () => {
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await screen.findByText("Here is the answer.");

    await user.click(thumbs()[0]);

    await waitFor(() => expect(sendFeedback).toHaveBeenCalled());
    expect(sendFeedback.mock.calls[0][0]).toBe("assistant-1");
  });

  it("a failed rating never interrupts the conversation", async () => {
    // Feedback is a nice-to-have. An error toast over a failed thumbs-up
    // would be more disruptive than the lost signal is worth -- which is why
    // the component swallows it, and why that is worth pinning.
    sendFeedback.mockRejectedValue(new Error("Feedback endpoint down"));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    const answerText = await screen.findByText("Here is the answer.");

    await user.click(thumbs()[0]);
    await waitFor(() => expect(sendFeedback).toHaveBeenCalled());

    // The answer is still there and no error surfaced.
    expect(answerText).toBeInTheDocument();
    expect(screen.queryByText(/feedback endpoint down/i)).not.toBeInTheDocument();
  });
});

// ======================================================================
// Answer mode: quick answer (rag) vs deep research (agent)
// ======================================================================

describe("answer mode", () => {
  const step = (tool: string, summary: string, args: Record<string, unknown> = {}) =>
    ({ tool, args, summary });

  /** An agent-mode reply: same shape as `answer`, plus mode and steps. */
  function agentAnswer(content: string, steps: unknown[], citations: unknown[] = []) {
    return { ...answer(content, citations), mode: "agent", steps };
  }

  it("defaults to quick answer and sends the rag mode", async () => {
    // The fast path is the default. Deep research costs several model calls,
    // so nobody should land in it without choosing to.
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    expect(screen.getByRole("radio", { name: /quick answer/i })).toHaveAttribute(
      "aria-checked", "true"
    );

    await ask(user, "A question");

    await waitFor(() => expect(askQuestion).toHaveBeenCalled());
    expect(askQuestion.mock.calls[0][2]).toBe("rag");
  });

  it("sends the agent mode once deep research is selected", async () => {
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    expect(screen.getByRole("radio", { name: /deep research/i })).toHaveAttribute(
      "aria-checked", "true"
    );

    await ask(user, "Compare the two policies");

    await waitFor(() => expect(askQuestion).toHaveBeenCalled());
    expect(askQuestion.mock.calls[0][1]).toBe("Compare the two policies");
    expect(askQuestion.mock.calls[0][2]).toBe("agent");
  });

  it("explains the tradeoff of the selected mode", async () => {
    // Without the hint, "Deep research" sounds strictly better, and people
    // would pick it for everything and wonder why answers got slow.
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await user.click(screen.getByRole("radio", { name: /deep research/i }));

    expect(screen.getByText(/slower/i)).toBeInTheDocument();
  });

  it("locks the toggle while a question is in flight", async () => {
    askQuestion.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");

    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /deep research/i })).toBeDisabled()
    );
    // The mode the request started with stays selected while it runs.
    expect(screen.getByRole("radio", { name: /quick answer/i })).toHaveAttribute(
      "aria-checked", "true"
    );
  });

  it("unlocks the toggle again after a failed question", async () => {
    // If `sending` stayed true after an error, the toggle would be stuck for
    // the rest of the session.
    askQuestion.mockRejectedValue(new Error("The model timed out."));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    expect(await screen.findByText(/model timed out/i)).toBeInTheDocument();

    expect(screen.getByRole("radio", { name: /quick answer/i })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /deep research/i })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /quick answer/i })).toHaveAttribute(
      "aria-checked", "true"
    );
  });

  it("says it is researching while a deep research question is in flight", async () => {
    askQuestion.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    await ask(user, "Compare the two policies");

    expect(await screen.findByText(/researching your documents/i)).toBeInTheDocument();
  });

  it("does not say it is researching for a quick answer", async () => {
    askQuestion.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /deep research/i })).toBeDisabled()
    );

    expect(screen.queryByText(/researching/i)).not.toBeInTheDocument();
  });

  it("is one tab stop, switched with the arrow keys", async () => {
    // The radio group keyboard contract: only the selected option is
    // tabbable, and the arrow keys move both the selection and the focus.
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    const quick = screen.getByRole("radio", { name: /quick answer/i });
    const deep = screen.getByRole("radio", { name: /deep research/i });
    expect(quick).toHaveAttribute("tabindex", "0");
    expect(deep).toHaveAttribute("tabindex", "-1");

    quick.focus();
    await user.keyboard("{ArrowRight}");
    expect(deep).toHaveAttribute("aria-checked", "true");
    expect(deep).toHaveFocus();
    expect(deep).toHaveAttribute("tabindex", "0");

    // Wraps around at the end.
    await user.keyboard("{ArrowRight}");
    expect(quick).toHaveAttribute("aria-checked", "true");
    expect(quick).toHaveFocus();
  });

  it("links the tradeoff hint to the mode control", async () => {
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    const group = screen.getByRole("radiogroup", { name: /answer mode/i });
    const hintId = group.getAttribute("aria-describedby");
    expect(hintId).toBeTruthy();
    expect(document.getElementById(hintId as string)).toHaveTextContent(/.+/);
  });

  it("shows how many research steps an agent answer took", async () => {
    askQuestion.mockResolvedValue(
      agentAnswer("The answer.", [
        step("search_documents", "Found 5 excerpts from 2 documents", { query: "leave" }),
        step("read_document", "Read page 4 of handbook.pdf"),
      ])
    );
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    await ask(user, "A question");

    expect(await screen.findByText(/research steps \(2\)/i)).toBeInTheDocument();
    // Collapsed until asked for, like the sources.
    expect(screen.queryByText(/Found 5 excerpts/)).not.toBeInTheDocument();
  });

  it("reveals each step's tool and summary, in order, when expanded", async () => {
    askQuestion.mockResolvedValue(
      agentAnswer("The answer.", [
        step("search_documents", "Found 5 excerpts from 2 documents", { query: "leave" }),
        step("read_document", "Read page 4 of handbook.pdf"),
      ])
    );
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await user.click(screen.getByRole("radio", { name: /deep research/i }));
    await ask(user, "A question");
    await user.click(await screen.findByText(/research steps/i));

    const first = await screen.findByText("search_documents");
    const second = screen.getByText("read_document");
    expect(screen.getByText(/Found 5 excerpts from 2 documents/)).toBeInTheDocument();
    expect(screen.getByText(/Read page 4 of handbook\.pdf/)).toBeInTheDocument();
    // The arguments too, so you can see WHAT the agent searched for.
    expect(screen.getByText(/query: "leave"/)).toBeInTheDocument();

    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("shows no steps toggle for a quick answer", async () => {
    // Rag mode returns an empty steps list; an empty "Research steps (0)"
    // control would only invite a pointless click.
    askQuestion.mockResolvedValue({ ...answer("The answer."), mode: "rag", steps: [] });
    const user = userEvent.setup();
    render(<ChatPanel projectId="p1" />);
    await waitFor(() => expect(createChatSession).toHaveBeenCalled());

    await ask(user, "A question");
    await screen.findByText("The answer.");

    expect(screen.queryByText(/research steps/i)).not.toBeInTheDocument();
  });
});
