// `components/SearchPanel.tsx` -- retrieval with no model involved.

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


describe("empty states", () => {
  it("before any search, does not claim nothing matched", async () => {
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
    search.mockResolvedValue(response([]));
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "something unfindable");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/threshold/i)).toBeInTheDocument();
  });
});


describe("running a search", () => {
  it("scopes the search to this project", async () => {
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


describe("displaying results", () => {
  it("shows the passage text", async () => {
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/1\.75 days of paid leave/)).toBeInTheDocument();
  });

  it("shows the source filename and page", async () => {
    const user = userEvent.setup();
    render(<SearchPanel projectId="p1" />);

    await user.type(box(), "annual leave");
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/handbook\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/page 12/i)).toBeInTheDocument();
  });

  it("shows the score as a percentage", async () => {
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
