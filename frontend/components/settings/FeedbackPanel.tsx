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
import { useT } from "@/lib/i18n";

const PERIODS = [7, 30, 90] as const;

/** 0.8123 -> "81%", null -> "—". */
export function formatSatisfaction(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export default function FeedbackPanel() {
  const { t } = useT();
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
      .catch((err) => !cancelled && setError(getErrorMessage(err, t("feedback.loadError"))));
    return () => {
      cancelled = true;
    };
    // `t` only words the fallback error; a language switch must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs" role="group" aria-label={t("feedback.period")}>
        {PERIODS.map((p) => (
          <button
            key={p}
            type="button"
            className={p === days ? "btn-primary text-xs" : "btn-ghost text-xs"}
            aria-pressed={p === days}
            onClick={() => setDays(p)}
          >
            {t("feedback.lastDays", { days: p })}
          </button>
        ))}
      </div>

      {error ? (
        <Notice tone="error">{error}</Notice>
      ) : !summary ? (
        <div className="glass shimmer h-40" aria-busy="true" />
      ) : summary.total === 0 ? (
        <div className="glass px-6 py-10 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
          {t("feedback.empty")}
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label={t("feedback.satisfaction")} value={formatSatisfaction(summary.satisfaction)} />
            <Stat label={t("feedback.helpful")} value={summary.up.toLocaleString()} />
            <Stat label={t("feedback.notHelpful")} value={summary.down.toLocaleString()} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Breakdown
              title={t("feedback.byMode")}
              rows={summary.by_mode}
              labels={{ rag: t("chat.mode.rag"), agent: t("chat.mode.agent") }}
            />
            <Breakdown title={t("feedback.byModel")} rows={summary.by_model} />
          </div>

          {summary.recent_negative.length > 0 && (
            <section aria-label={t("feedback.recent")} className="space-y-3">
              <h3 className="text-sm font-semibold">{t("feedback.recent")}</h3>
              {summary.recent_negative.map((item) => (
                <article key={`${item.message_id}-${item.created_at}`} className="glass space-y-2 p-4 text-sm">
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {new Date(item.created_at).toLocaleString()}
                    {item.mode ? ` · ${item.mode === "agent" ? t("chat.mode.agent") : t("chat.mode.rag")}` : ""}
                    {item.model ? ` · ${item.model}` : ""}
                  </div>
                  <p>
                    <strong>Q:</strong> {item.question ?? t("feedback.questionMissing")}
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
  const { t } = useT();
  return (
    <table className="glass w-full text-left text-sm">
      <caption className="px-4 pt-3 text-left text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        {title}
      </caption>
      <thead style={{ color: "var(--text-muted)" }}>
        <tr>
          <th className="px-4 py-2 font-medium">{t("feedback.slice")}</th>
          <th className="px-4 py-2 font-medium">{t("feedback.up")}</th>
          <th className="px-4 py-2 font-medium">{t("feedback.down")}</th>
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
