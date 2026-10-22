/**
 * SearchPanel.test.tsx
 * ====================
 * WHAT THIS FILE TESTS
 * --------------------
 * `components/SearchPanel.tsx` -- retrieval with no model involved.
 *
 * WHY THIS COMPONENT IS WORTH TESTING CAREFULLY
 * ---------------------------------------------
 * It is the project's debugging instrument. When a chat answer is wrong there
 * are two possible causes with completely different fixes -- the right passage
 * was retrieved and the model misused it, or the right passage was never
 * retrieved. This panel is what distinguishes them, so the score and the
 * source on each result are not decoration; they are the readout.
 *
 * THE DISTINCTION THAT MATTERS MOST
 * ---------------------------------
 * "You have not searched yet" and "your search matched nothing" must look
 * different. Collapsing them is the single most common empty-state bug, and
 * here it would be actively misleading: a user who has just opened the tab
 * would be told their documents contain no match for a query they never ran.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SearchPanel from "@/components/SearchPanel";

const search = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { search: (...args: unknown[]) => search(...args) },
  getErrorMessage: (error: unknown, fallback = "Something went wrong.") =>
    error instanceof Error && error.message ? error.message : fallback,
}));

/** One search result, shaped the way the backend returns them. */
function result(overrides: Record<string, unknown> = {}) {
  return {
    chunk_id: "c1",
    document_id: "d1",
    filename: "handbook.pdf",
    page_number: 12,
    chunk_index: 0,
    content: "Employees accrue 1.75 days of paid leave per month.",
    score: 0.83,
    ...overrides,
  };
}

function response(results: unknown[], took = 0.12) {
  return { results, took_seconds: took, query: "q" };
}

const box = () => screen.getByPlaceholderText(/search by meaning/i);

beforeEach(() => {
  search.mockReset();
  search.mockResolvedValue(response([result()]));
});

// ======================================================================
// The two empty states
// ======================================================================

describe("empty states", () => {
  it("before any search, does not claim nothing matched", async () => {
    // The bug this guards: telling a user their documents contain no match
    // for a query they have not typed.
    render(<SearchPanel projectId="p1" />);

    expect(screen.queryByText(/nothing matched/i)).not.toBeInTheDocument();
  });

  it("after a search with no results, says nothing matched", async () => {
    search.mockResolvedValue(response([]));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "something unfindable");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/nothing matched/i)).toBeInTheDocument();
  });

  it("explains WHY nothing matched", async () => {
    // "No results" invites the conclusion that search is broken. Saying that
    // everything scored below the threshold, deliberately, is the difference
    // between a dead end and an actionable message.
    search.mockResolvedValue(response([]));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "something unfindable");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/threshold/i)).toBeInTheDocument();
  });
});

// ======================================================================
// Running a search
// ======================================================================

describe("running a search", () => {
  it("scopes the search to this project", async () => {
    // Why a search in the HR project does not return finance passages.
    const user = userEvent.setup();
    render(<SearchPanel projectId="project-42" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(search).toHaveBeenCalled());
    expect(search.mock.calls[0][1]).toBe("project-42");
  });

  it("trims the query before sending it", async () => {
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "   annual leave   ");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(search).toHaveBeenCalled());
    expect(search.mock.calls[0][0]).toBe("annual leave");
  });

  it("does not search on an empty query", async () => {
    // An empty search box is a mistake, not a search. Sending it would be
    // refused by the backend with a 422 and shown to the user as an error.
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.click(box());
    await user.keyboard("{Enter}");

    expect(search).not.toHaveBeenCalled();
  });

  it("does not search on whitespace alone", async () => {
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "    ");
    await user.keyboard("{Enter}");

    expect(search).not.toHaveBeenCalled();
  });

  it("searches when the button is clicked", async () => {
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.click(screen.getByRole("button", { name: /search/i }));

    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));
  });
});

// ======================================================================
// The readout
// ======================================================================

describe("displaying results", () => {
  it("shows the passage text", async () => {
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/1\.75 days of paid leave/)).toBeInTheDocument();
  });

  it("shows the source filename and page", async () => {
    // Without these the panel cannot answer "where did this come from?",
    // which is most of its purpose.
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/handbook\.pdf/)).toBeInTheDocument();
    // Anchored to the word, not a bare /12/ -- which also matches the "1.75"
    // and "12" inside the passage text itself.
    expect(screen.getByText(/page 12/i)).toBeInTheDocument();
  });

  it("shows the score as a percentage", async () => {
    // 83% is judgeable at a glance; 0.83 is not.
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/83% match/)).toBeInTheDocument();
  });

  it("renders every result", async () => {
    search.mockResolvedValue(
      response([
        result({ chunk_id: "c1", content: "First distinct passage." }),
        result({ chunk_id: "c2", content: "Second distinct passage." }),
        result({ chunk_id: "c3", content: "Third distinct passage." }),
      ])
    );
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "passages");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/First distinct/)).toBeInTheDocument();
    expect(screen.getByText(/Second distinct/)).toBeInTheDocument();
    expect(screen.getByText(/Third distinct/)).toBeInTheDocument();
  });

  it("handles a result with no page number", async () => {
    // A CSV or an email has no pages. This must not render "page null" or
    // crash on a missing field.
    search.mockResolvedValue(
      response([result({ filename: "data.csv", page_number: null })])
    );
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "rows");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/data\.csv/)).toBeInTheDocument();
    expect(screen.queryByText(/page null/i)).not.toBeInTheDocument();
  });
});

describe("score bands", () => {
  // The colour is how match quality reads at a glance. The exact class does
  // not matter; that the three bands are DISTINGUISHED does -- otherwise a
  // 20% match looks as confident as a 90% one.
  it.each([
    ["a strong match", 0.83, "83% match"],
    ["a middling match", 0.4, "40% match"],
    ["a weak match", 0.18, "18% match"],
  ])("renders %s", async (_label, score, expected) => {
    search.mockResolvedValue(response([result({ score })]));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "query");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("gives strong and weak matches different styling", async () => {
    search.mockResolvedValue(
      response([
        result({ chunk_id: "strong", score: 0.9, content: "Strong passage." }),
        result({ chunk_id: "weak", score: 0.16, content: "Weak passage." }),
      ])
    );
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "query");
    await user.keyboard("{Enter}");

    const strong = await screen.findByText(/90% match/);
    const weak = screen.getByText(/16% match/);

    expect(strong.className).not.toBe(weak.className);
  });
});

// ======================================================================
// Failure
// ======================================================================

describe("when the search fails", () => {
  it("shows the reason", async () => {
    search.mockRejectedValue(new Error("Project not found."));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/project not found/i)).toBeInTheDocument();
  });

  it("falls back to a readable message", async () => {
    search.mockRejectedValue(new Error(""));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/search failed/i)).toBeInTheDocument();
  });

  it("does not claim nothing matched", async () => {
    // A failed search and an empty search are different things. Showing
    // "nothing matched closely enough" after a 500 would send the user off
    // rephrasing a query that never reached the server.
    search.mockRejectedValue(new Error("Internal server error"));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    await screen.findByText(/internal server error/i);
    expect(screen.queryByText(/nothing matched/i)).not.toBeInTheDocument();
  });

  it("clears a previous error on the next search", async () => {
    search.mockRejectedValueOnce(new Error("First attempt failed."));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "query one");
    await user.keyboard("{Enter}");
    await screen.findByText(/first attempt failed/i);

    search.mockResolvedValue(response([result()]));
    await user.clear(box());
    await user.type(box(), "query two");
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(screen.queryByText(/first attempt failed/i)).not.toBeInTheDocument()
    );
  });
});

// ======================================================================
// Batch 2: the document filter and hybrid-search labels
// ======================================================================

describe("limiting the search to some documents", () => {
  const docs = [
    { id: "d1", filename: "handbook.pdf", status: "completed" },
    { id: "d2", filename: "expenses.docx", status: "completed" },
  ] as never[];

  it("sends no filter until documents are ticked, then only those", async () => {
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" documents={docs} />);

    await user.type(box(), "travel{Enter}");
    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));
    expect(search.mock.calls[0]).toEqual(["travel", "p1", 10]);

    await user.click(screen.getByRole("button", { name: /all documents/i }));
    await user.click(screen.getByRole("option", { name: "handbook.pdf" }));
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(search).toHaveBeenCalledTimes(2));
    expect(search.mock.calls[1]).toEqual(["travel", "p1", 10, ["d1"]]);
  });

  it("says which half of hybrid search found a result", async () => {
    search.mockResolvedValue(response([result({ match_type: "keyword" })]));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "FIN-204{Enter}");

    expect(await screen.findByText(/found by exact words/i)).toBeInTheDocument();
  });
});
