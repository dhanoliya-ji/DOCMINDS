// Renders an assistant answer: Markdown, plus the two things answers about numbers now...

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
