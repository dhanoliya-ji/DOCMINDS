/**
 * Collab.test.tsx
 * ===============
 * The collaboration batch, in the browser:
 *
 *   charts          parseChartSpec rejects what it cannot draw; AnswerChart
 *                   draws bars / lines / pies with a legend and a data table
 *   markdown        AnswerMarkdown renders GFM tables and ```chart blocks
 *   confidence      the badge names its level and explains it on demand
 *   share dialog    create (token shown once, copied), list, revoke
 *   prompt library  loads on first open, inserts, saves, org scope gated
 *   compare         two documents in, a diff with expandable passages out
 *   public page     an answer, a project, and the one dead-link message
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnswerChart, { parseChartSpec } from "@/components/AnswerChart";
import AnswerMarkdown from "@/components/AnswerMarkdown";
import ComparePanel from "@/components/ComparePanel";
import ConfidenceBadge from "@/components/ConfidenceBadge";
import PromptLibrary from "@/components/PromptLibrary";
import ShareDialog from "@/components/ShareDialog";
import SharedPage from "@/app/share/[token]/page";
import type { DocumentItem } from "@/lib/api";

// vi.hoisted: vi.mock factories run before the module body.
const { calls, copyText } = vi.hoisted(() => ({
  calls: {
    createShareLink: vi.fn(),
    listShareLinks: vi.fn(),
    revokeShareLink: vi.fn(),
    getPublicShare: vi.fn(),
    listPrompts: vi.fn(),
    createPrompt: vi.fn(),
    deletePrompt: vi.fn(),
    compareDocuments: vi.fn(),
  },
  copyText: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: Object.fromEntries(
      Object.keys(calls).map((name) => [name, (...a: unknown[]) => calls[name as keyof typeof calls](...a)])
    ),
  };
});

vi.mock("@/lib/export", async () => {
  const actual = await vi.importActual<typeof import("@/lib/export")>("@/lib/export");
  return { ...actual, copyText: (...a: unknown[]) => copyText(...a) };
});

beforeEach(() => {
  Object.values(calls).forEach((fn) => fn.mockReset());
  copyText.mockReset().mockResolvedValue(true);
});

const BAR = JSON.stringify({
  type: "bar",
  title: "Refunds per quarter",
  labels: ["Q1", "Q2"],
  series: [
    { name: "2023", values: [12, 18] },
    { name: "2024", values: [10, 21] },
  ],
  unit: "%",
});

// ======================================================================
// Charts
// ======================================================================

describe("parseChartSpec", () => {
  it("accepts a well-formed spec", () => {
    expect(parseChartSpec(BAR)?.series).toHaveLength(2);
  });

  it.each([
    ["not json", "{oops"],
    ["unknown type", JSON.stringify({ type: "radar", labels: ["a"], series: [{ name: "x", values: [1] }] })],
    ["length mismatch", JSON.stringify({ type: "bar", labels: ["a", "b"], series: [{ name: "x", values: [1] }] })],
    ["not a number", JSON.stringify({ type: "line", labels: ["a"], series: [{ name: "x", values: ["many"] }] })],
    ["pie with two series", JSON.stringify({ type: "pie", labels: ["a"], series: [{ values: [1] }, { values: [2] }] })],
    ["negative pie", JSON.stringify({ type: "pie", labels: ["a", "b"], series: [{ values: [1, -2] }] })],
  ])("rejects %s", (_name, source) => {
    expect(parseChartSpec(source)).toBeNull();
  });
});

describe("AnswerChart", () => {
  it("draws one bar per value, a legend and a data table", async () => {
    const user = userEvent.setup();
    const { container } = render(<AnswerChart source={BAR} />);
    expect(screen.getByText("Refunds per quarter")).toBeInTheDocument();
    expect(container.querySelectorAll("path[role='img']")).toHaveLength(4);
    expect(screen.getByRole("img", { name: "2024, Q2: 21%" })).toBeInTheDocument();
    // Two series -> a legend, so identity is never colour alone.
    expect(screen.getAllByText("2023").length).toBeGreaterThan(0);

    await user.click(screen.getByText("Show the data"));
    const table = screen.getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "Q1" })).toBeInTheDocument();
    expect(within(table).getByText("18%")).toBeInTheDocument();
  });

  it("shows a value on hover or focus", async () => {
    const user = userEvent.setup();
    render(<AnswerChart source={BAR} />);
    await user.tab();
    expect(screen.getByRole("presentation")).toHaveTextContent("2023, Q1: 12%");
  });

  it("labels pie slices with their share", () => {
    render(
      <AnswerChart
        source={JSON.stringify({ type: "pie", labels: ["Yes", "No"], series: [{ name: "Votes", values: [3, 1] }] })}
      />
    );
    expect(screen.getByRole("img", { name: "Yes: 3 (75%)" })).toBeInTheDocument();
    expect(screen.getByText(/No · 25%/)).toBeInTheDocument();
  });

  it("falls back to the raw data when the spec is unusable", () => {
    render(<AnswerChart source="{broken" />);
    expect(screen.getByText(/could not be drawn/i)).toBeInTheDocument();
    expect(screen.getByText("{broken")).toBeInTheDocument();
  });
});

describe("AnswerMarkdown", () => {
  it("renders GFM tables and chart blocks", () => {
    const answer = [
      "Refunds rose [1].",
      "",
      "| Quarter | Rate |",
      "|---|---|",
      "| Q1 | 12 |",
      "",
      "```chart",
      BAR,
      "```",
    ].join("\n");
    const { container } = render(<AnswerMarkdown>{answer}</AnswerMarkdown>);
    expect(container.querySelector(".answer-table-wrap table")).not.toBeNull();
    expect(screen.getByRole("columnheader", { name: "Quarter" })).toBeInTheDocument();
    expect(screen.getByText("Refunds per quarter")).toBeInTheDocument();
    expect(container.querySelector("pre")).toBeNull();
  });

  it("never renders raw HTML from an answer", () => {
    const { container } = render(<AnswerMarkdown>{"Hi <img src=x onerror=alert(1)>"}</AnswerMarkdown>);
    expect(container.querySelector("img")).toBeNull();
  });
});

// ======================================================================
// Confidence
// ======================================================================

describe("ConfidenceBadge", () => {
  it("names the level and lists the reasons", async () => {
    const user = userEvent.setup();
    render(
      <ConfidenceBadge
        confidence={{
          level: "medium", score: 0.5, retrieval: 0.6, coverage: 0.4,
          reasons: [{ code: "strong_match", value: 0.82 }, { code: "single_source" }],
        }}
      />
    );
    const badge = screen.getByRole("button", { name: /medium confidence/i });
    await user.click(badge);
    expect(badge).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/matches the question closely \(82%\)/)).toBeInTheDocument();
    expect(screen.getByText(/single document/)).toBeInTheDocument();
  });

  it("renders nothing without a verdict", () => {
    const { container } = render(<ConfidenceBadge confidence={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// ======================================================================
// Share dialog
// ======================================================================

const link = (over: Record<string, unknown> = {}) => ({
  id: "s1", kind: "message", target_id: "m1", token_prefix: "dms_abcdef", created_by: "u1",
  created_at: "2026-10-01T00:00:00Z", expires_at: null, revoked_at: null, view_count: 2,
  last_viewed_at: null, status: "active", ...over,
});

describe("ShareDialog", () => {
  it("creates a link, shows it once and copies it", async () => {
    const user = userEvent.setup();
    calls.listShareLinks.mockResolvedValue([]);
    calls.createShareLink.mockResolvedValue({ ...link({ id: "s2" }), token: "dms_secret", path: "/share/dms_secret" });
    render(<ShareDialog kind="message" targetId="m1" onClose={() => {}} />);

    await waitFor(() => expect(calls.listShareLinks).toHaveBeenCalledWith("message", "m1"));
    await user.selectOptions(screen.getByLabelText("Link expires"), "30");
    await user.click(screen.getByRole("button", { name: "Create link" }));

    expect(calls.createShareLink).toHaveBeenCalledWith("message", "m1", 30);
    const url = await screen.findByDisplayValue(/\/share\/dms_secret$/);
    expect(screen.getByText(/shown only once/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /copy link/i }));
    expect(copyText).toHaveBeenCalledWith((url as HTMLInputElement).value);
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("lists links and revokes one", async () => {
    const user = userEvent.setup();
    calls.listShareLinks.mockResolvedValue([link()]);
    calls.revokeShareLink.mockResolvedValue({});
    render(<ShareDialog kind="session" targetId="c1" onClose={() => {}} />);

    expect(await screen.findByText(/2 views/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    expect(calls.revokeShareLink).toHaveBeenCalledWith("s1");
    expect(await screen.findByText(/Revoked/)).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    calls.listShareLinks.mockResolvedValue([]);
    render(<ShareDialog kind="project" targetId="p1" onClose={onClose} />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

// ======================================================================
// Prompt library
// ======================================================================

const prompt = (id: string, scope: string, title: string, over: Record<string, unknown> = {}) => ({
  id, scope, title, body: `${title} body`, editable: scope !== "builtin", key: null, ...over,
});

describe("PromptLibrary", () => {
  it("loads on first open and inserts a prompt", async () => {
    const user = userEvent.setup();
    const onInsert = vi.fn();
    calls.listPrompts.mockResolvedValue([
      prompt("builtin:summarise-risks", "builtin", "Summarise risks", { key: "summarise-risks", editable: false }),
      prompt("p1", "personal", "My check"),
    ]);
    render(<PromptLibrary draft="" canShare={false} onInsert={onInsert} />);
    expect(calls.listPrompts).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /prompts/i }));
    await user.click(await screen.findByRole("button", { name: "Summarise risks" }));
    expect(onInsert).toHaveBeenCalledWith("Summarise risks body");
    // Built-ins cannot be deleted; one's own can.
    await user.click(screen.getByRole("button", { name: /prompts/i }));
    expect(screen.queryByRole("button", { name: /delete summarise risks/i })).toBeNull();
    expect(screen.getByRole("button", { name: /delete my check/i })).toBeInTheDocument();
  });

  it("saves the current question, offering the team library only to editors", async () => {
    const user = userEvent.setup();
    calls.listPrompts.mockResolvedValue([]);
    calls.createPrompt.mockResolvedValue(prompt("p9", "personal", "Dates"));
    const { rerender } = render(<PromptLibrary draft="List all dates" canShare={false} onInsert={() => {}} />);
    await user.click(screen.getByRole("button", { name: /prompts/i }));

    const scope = await screen.findByLabelText("Who can use it");
    expect(within(scope).queryByRole("option", { name: /shared with the team/i })).toBeNull();
    rerender(<PromptLibrary draft="List all dates" canShare onInsert={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Who can use it"), "org");
    await user.type(screen.getByLabelText("Title"), "Dates");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(calls.createPrompt).toHaveBeenCalledWith("Dates", "List all dates", "org");
  });
});

// ======================================================================
// Compare
// ======================================================================

const doc = (id: string, filename: string) => ({ id, filename, status: "completed" }) as unknown as DocumentItem;

describe("ComparePanel", () => {
  it("needs two documents", () => {
    render(<ComparePanel documents={[doc("d1", "a.pdf")]} />);
    expect(screen.getByText(/at least two documents/i)).toBeInTheDocument();
  });

  it("compares and shows each difference with its passages", async () => {
    const user = userEvent.setup();
    calls.compareDocuments.mockResolvedValue({
      document_a: { id: "d1", filename: "2023.pdf" }, document_b: { id: "d2", filename: "2024.pdf" },
      summary: "The notice period doubled.", alignment: "lexical", pairs: 1, identical: 3,
      model: "mock", tokens_used: 0, latency: 0.1,
      differences: [{ kind: "changed", topic: "Notice period", detail: "30 days vs 60 days", a: [1], b: [1] }],
      citations: [
        { side: "a", number: 1, label: "A1", chunk_id: "c1", document_id: "d1", filename: "2023.pdf", page_number: 4, snippet: "thirty days" },
        { side: "b", number: 1, label: "B1", chunk_id: "c2", document_id: "d2", filename: "2024.pdf", page_number: 5, snippet: "sixty days" },
      ],
    });
    render(<ComparePanel documents={[doc("d1", "2023.pdf"), doc("d2", "2024.pdf")]} />);
    await user.type(screen.getByLabelText(/focus/i), "termination");
    await user.click(screen.getByRole("button", { name: "Compare" }));

    expect(calls.compareDocuments).toHaveBeenCalledWith("d1", "d2", "termination");
    expect(await screen.findByText("The notice period doubled.")).toBeInTheDocument();
    expect(screen.getByText("Changed")).toBeInTheDocument();
    expect(screen.getByText(/1 differing sections, 3 unchanged/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "B1" }));
    expect(screen.getByText("sixty days")).toBeInTheDocument();
  });

  it("swaps the two documents", async () => {
    const user = userEvent.setup();
    calls.compareDocuments.mockResolvedValue(null);
    render(<ComparePanel documents={[doc("d1", "a.pdf"), doc("d2", "b.pdf")]} />);
    await user.click(screen.getByRole("button", { name: /swap/i }));
    await user.click(screen.getByRole("button", { name: "Compare" }));
    expect(calls.compareDocuments).toHaveBeenCalledWith("d2", "d1", "");
  });
});

// ======================================================================
// The public page
// ======================================================================

async function renderShared(token = "dms_tok") {
  // An already-settled thenable, as Next.js hands a resolved route segment
  // to `use()` (see ProjectPage.test.tsx for why).
  const params = Object.assign(Promise.resolve({ token }), { status: "fulfilled", value: { token } });
  render(<SharedPage params={params} />);
}

describe("public share page", () => {
  it("shows a shared answer with its sources and confidence", async () => {
    calls.getPublicShare.mockResolvedValue({
      kind: "message", title: "Refunds", created_at: null, expires_at: null, documents: [],
      messages: [
        { role: "user", content: "How long do refunds take?", citations: [] },
        {
          role: "assistant", content: "Thirty days [1].", mode: "rag",
          confidence: { level: "high", score: 0.8, retrieval: 0.8, coverage: 1, reasons: [] },
          citations: [{ number: 1, filename: "policy.pdf", page_number: 2, score: 0.8, snippet: "Within thirty days." }],
        },
      ],
    });
    await renderShared();
    expect(await screen.findByRole("heading", { name: "Refunds" })).toBeInTheDocument();
    expect(calls.getPublicShare).toHaveBeenCalledWith("dms_tok");
    expect(screen.getByText("Shared answer")).toBeInTheDocument();
    expect(screen.getByText("How long do refunds take?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /high confidence/i })).toBeInTheDocument();
    expect(screen.getByText(/\[1\] policy\.pdf/)).toBeInTheDocument();
  });

  it("lists a shared project's documents", async () => {
    calls.getPublicShare.mockResolvedValue({
      kind: "project", title: "Handbooks", messages: [],
      documents: [{ filename: "policy.pdf", file_type: "pdf", page_count: 12 }],
    });
    await renderShared();
    expect(await screen.findByText("policy.pdf")).toBeInTheDocument();
    expect(screen.getByText(/12 pages/)).toBeInTheDocument();
  });

  it("gives one message for every dead link", async () => {
    calls.getPublicShare.mockRejectedValue(new Error("404"));
    await renderShared();
    expect(await screen.findByText(/does not exist, has expired or was revoked/i)).toBeInTheDocument();
  });
});
