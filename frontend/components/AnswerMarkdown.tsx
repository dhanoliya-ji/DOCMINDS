/**
 * AnswerMarkdown.tsx
 * ==================
 * Renders an assistant answer: Markdown, plus the two things answers about
 * numbers now use (backend services/llm.py, rule 7):
 *
 *     | Quarter | Refunds |      a GitHub-style table (remark-gfm), wrapped
 *     |---------|---------|      so a wide one scrolls inside the bubble
 *                                instead of stretching the page
 *     ```chart                   a chart, drawn by AnswerChart.tsx with its
 *     {"type": "bar", ...}       data table underneath
 *     ```
 *
 * Used by the chat panel and the public share page, so a shared answer
 * looks exactly like the original.
 *
 * Raw HTML in the Markdown is NOT rendered (react-markdown's default): an
 * answer quotes uploaded documents, and those must never inject markup.
 */

"use client";

import type { ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import AnswerChart from "@/components/AnswerChart";

type CodeProps = ComponentProps<"code"> & { node?: unknown };

const components = {
  table: ({ children }: ComponentProps<"table">) => (
    <div className="answer-table-wrap">
      <table>{children}</table>
    </div>
  ),
  code: ({ className, children, ...props }: CodeProps) => {
    // `node` is react-markdown's syntax-tree node: not a DOM attribute.
    const rest = { ...props };
    delete rest.node;
    if (/\blanguage-chart\b/.test(className ?? "")) {
      return <AnswerChart source={String(children ?? "").trim()} />;
    }
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    );
  },
  // A chart block arrives as <pre><code>; the chart must not sit in a <pre>.
  pre: ({ children }: ComponentProps<"pre">) => {
    const child = Array.isArray(children) ? children[0] : children;
    const className = (child as { props?: { className?: string } })?.props?.className ?? "";
    if (/\blanguage-chart\b/.test(className)) return <>{children}</>;
    return <pre className="overflow-x-auto rounded-lg p-2 text-xs" style={{ background: "var(--surface-1)" }}>{children}</pre>;
  },
};

export default function AnswerMarkdown({ children }: { children: string }) {
  return (
    <div className="prose-invert space-y-2">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
