/**
 * FeedbackPanel.tsx
 * =================
 * How people rate the organization's answers (GET /org/feedback): the thumbs
 * up / thumbs down from every chat, summarised for an admin.
 *
 * WHAT TO DO WITH IT
 * ------------------
 * The headline number is satisfaction, but the useful part is the list of
 * recent thumbs-down answers with their questions. A cluster of them about
 * one document usually means it chunked or OCR'd badly -- re-index it with
 * different chunking from the project page. And each of those questions is a
 * ready-made case for the evaluation golden set (backend/app/eval), so the
 * fix can be measured instead of hoped for.
 */

"use client";

import { useEffect, useState } from "react";

import { Notice } from "@/components/AuthCard";
import { FeedbackBreakdown, FeedbackSummary, api, getErrorMessage } from "@/lib/api";

const PERIODS = [7, 30, 90] as const;

/** 0.8123 -> "81%", null -> "—". */
export function formatSatisfaction(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export default function FeedbackPanel() {
  const [days, setDays] = useState<number>(30);
  const [summary, setSummary] = useState<FeedbackSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .getFeedbackSummary(days)
      .then((s) => {
        if (cancelled) return;
        setSummary(s);
        setError("");
      })
      .catch((err) => !cancelled && setError(getErrorMessage(err, "Could not load feedback.")));
    return () => {
      cancelled = true;
    };
  }, [days]);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs" role="group" aria-label="Period">
        {PERIODS.map((p) => (
          <button
            key={p}
            type="button"
            className={p === days ? "btn-primary text-xs" : "btn-ghost text-xs"}
            aria-pressed={p === days}
            onClick={() => setDays(p)}
          >
            Last {p} days
          </button>
        ))}
      </div>

      {error ? (
        <Notice tone="error">{error}</Notice>
      ) : !summary ? (
        <div className="glass shimmer h-40" aria-busy="true" />
      ) : summary.total === 0 ? (
        <div className="glass px-6 py-10 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
          No answers have been rated in this period yet. Ratings come from the
          thumbs up / down under every answer in Ask AI.
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Satisfaction" value={formatSatisfaction(summary.satisfaction)} />
            <Stat label="Helpful" value={summary.up.toLocaleString()} />
            <Stat label="Not helpful" value={summary.down.toLocaleString()} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Breakdown title="By answer mode" rows={summary.by_mode} labels={{ rag: "Quick answer", agent: "Deep research" }} />
            <Breakdown title="By model" rows={summary.by_model} />
          </div>

          {summary.recent_negative.length > 0 && (
            <section aria-label="Recent unhelpful answers" className="space-y-3">
              <h3 className="text-sm font-semibold">Recent unhelpful answers</h3>
              {summary.recent_negative.map((item) => (
                <article key={`${item.message_id}-${item.created_at}`} className="glass space-y-2 p-4 text-sm">
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {new Date(item.created_at).toLocaleString()}
                    {item.mode ? ` · ${item.mode === "agent" ? "Deep research" : "Quick answer"}` : ""}
                    {item.model ? ` · ${item.model}` : ""}
                  </div>
                  <p>
                    <strong>Q:</strong> {item.question ?? "(question not found)"}
                  </p>
                  <p style={{ color: "var(--text-secondary)" }}>
                    <strong>A:</strong> {item.answer.length > 400 ? `${item.answer.slice(0, 400)}...` : item.answer}
                  </p>
                  {item.comment && (
                    <p className="text-xs" style={{ color: "var(--warning)" }}>
                      “{item.comment}”
                    </p>
                  )}
                </article>
              ))}
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="glass p-4">
      <div className="text-xs" style={{ color: "var(--text-muted)" }}>
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function Breakdown({
  title,
  rows,
  labels = {},
}: {
  title: string;
  rows: FeedbackBreakdown[];
  labels?: Record<string, string>;
}) {
  return (
    <table className="glass w-full text-left text-sm">
      <caption className="px-4 pt-3 text-left text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        {title}
      </caption>
      <thead style={{ color: "var(--text-muted)" }}>
        <tr>
          <th className="px-4 py-2 font-medium">Slice</th>
          <th className="px-4 py-2 font-medium">Up</th>
          <th className="px-4 py-2 font-medium">Down</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
            <td className="px-4 py-2">{labels[row.key] ?? row.key}</td>
            <td className="px-4 py-2">{row.up}</td>
            <td className="px-4 py-2">{row.down}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
