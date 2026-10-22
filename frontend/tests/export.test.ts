/**
 * export.test.ts / highlighting
 * =============================
 * WHAT THIS FILE TESTS
 * --------------------
 *   lib/export.ts     the Markdown a copied answer or exported conversation
 *                     becomes -- sources included, numbered as in the answer
 *   lib/highlight.ts  finding a cited passage in text whose whitespace and
 *                     case differ from the snippet, and which PDF text runs
 *                     belong to it
 */

import { describe, expect, it } from "vitest";

import {
  answerToMarkdown,
  citationLine,
  conversationToMarkdown,
  safeFilename,
} from "@/lib/export";
import { findPassage, matchingItems, normalizeText } from "@/lib/highlight";
import type { ChatMessageItem, CitationItem } from "@/lib/api";

const cite = (n: number, extra: Partial<CitationItem> = {}): CitationItem => ({
  number: n,
  chunk_id: `c${n}`,
  document_id: `d${n}`,
  filename: "handbook.pdf",
  page_number: 12,
  score: 0.8,
  snippet: 'Staff get "25 days" of leave.',
  ...extra,
});

const message = (role: string, content: string, extra: Partial<ChatMessageItem> = {}): ChatMessageItem => ({
  id: `${role}-1`,
  session_id: "s1",
  role,
  content,
  tokens_used: null,
  latency: null,
  citations: [],
  created_at: "2026-10-23T14:02:00Z",
  ...extra,
});

describe("export", () => {
  it("a citation line", () => {
    expect(citationLine(cite(1))).toBe("1. handbook.pdf, page 12 -- \"Staff get '25 days' of leave.\"");
    expect(citationLine(cite(2, { page_number: null, snippet: "" }))).toBe("2. handbook.pdf");
  });

  it("an answer carries its sources", () => {
    const markdown = answerToMarkdown(message("assistant", "25 days [1].", { citations: [cite(1)] }));
    expect(markdown).toBe(
      "25 days [1].\n\n**Sources**\n\n1. handbook.pdf, page 12 -- \"Staff get '25 days' of leave.\""
    );
  });

  it("a conversation", () => {
    const markdown = conversationToMarkdown(
      "Leave questions",
      [
        message("user", "How much leave?"),
        message("assistant", "25 days [1].", { citations: [cite(1)], mode: "agent", model: "gpt-oss" }),
      ],
      new Date("2026-10-23T14:02:00Z")
    );
    expect(markdown).toMatch(/^# Leave questions\n\n_Exported from DocMinds on 2026-10-23 14:02 UTC_/);
    expect(markdown).toContain("## Question\n\nHow much leave?");
    expect(markdown).toContain("## Answer  (Deep research · gpt-oss)");
    expect(markdown.endsWith("\n")).toBe(true);
  });

  it("long snippets are shortened", () => {
    const line = citationLine(cite(1, { snippet: "word ".repeat(100) }));
    expect(line.endsWith('..."')).toBe(true);
    expect(line.length).toBeLessThan(260);
  });

  it("safe filenames", () => {
    expect(safeFilename("Q3: Leave / Policy?")).toBe("q3-leave-policy.md");
    expect(safeFilename("")).toBe("conversation.md");
  });
});

describe("finding a passage", () => {
  it("normalises case and whitespace", () => {
    expect(normalizeText("  Annual\n  LEAVE\tpolicy ")).toBe("annual leave policy");
  });

  it("maps the match back onto the original text", () => {
    const content = "Intro.\nEmployees   accrue\n1.75 days per month. Outro.";
    const range = findPassage(content, "employees accrue 1.75 days per month.");
    expect(range).not.toBeNull();
    const [start, end] = range!;
    expect(content.slice(start, end)).toBe("Employees   accrue\n1.75 days per month.");
  });

  it("ignores a trailing ellipsis on the snippet", () => {
    const content = "The policy says thirty days.";
    const [start, end] = findPassage(content, "The policy says...")!;
    expect(content.slice(start, end)).toBe("The policy says");
  });

  it("returns null when the passage is not there", () => {
    expect(findPassage("Something else entirely.", "annual leave")).toBeNull();
    expect(findPassage("Anything", "")).toBeNull();
  });

  it("marks the PDF text runs inside the snippet, skipping tiny ones", () => {
    const items = [
      { str: "Chapter 4" },
      { str: "Employees accrue 1.75" },
      { str: "days per month." },
      { str: "of" },
      { str: "Unrelated footer text" },
    ];
    const snippet = "Employees accrue 1.75 days per month. Unused days roll over.";
    expect(matchingItems(items, snippet)).toEqual([1, 2]);
  });
});
