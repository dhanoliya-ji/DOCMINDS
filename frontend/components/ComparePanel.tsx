// The project page's "Compare" tab: pick two documents (and, optionally, a focus such as...

"use client";

import { ArrowLeftRight, Loader2, Minus, PencilLine, Plus } from "lucide-react";
import { useState } from "react";

import {
  api,
  getErrorMessage,
  type Comparison,
  type ComparisonCitation,
  type DocumentItem,
} from "@/lib/api";
import { useT } from "@/lib/i18n";

const KIND_STYLE = {
  added: { icon: Plus, color: "var(--success)" },
  removed: { icon: Minus, color: "var(--danger)" },
  changed: { icon: PencilLine, color: "var(--warning)" },
} as const;

export default function ComparePanel({ documents }: { documents: DocumentItem[] }) {
  const { t } = useT();
  const [first, setFirst] = useState(documents[0]?.id ?? "");
  const [second, setSecond] = useState(documents[1]?.id ?? "");
  const [focus, setFocus] = useState("");
  const [result, setResult] = useState<Comparison | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [openPassage, setOpenPassage] = useState<string | null>(null);

  if (documents.length < 2) {
    return (
      <div className="glass px-6 py-16 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
        {t("compare.needTwo")}
      </div>
    );
  }

  async function run() {
    if (!first || !second || first === second) return;
    setRunning(true);
    setError("");
    try {
      setResult(await api.compareDocuments(first, second, focus));
      setOpenPassage(null);
    } catch (err) {
      setError(getErrorMessage(err, "Could not compare the documents."));
    } finally {
      setRunning(false);
    }
  }

  const byLabel = new Map<string, ComparisonCitation>(
    (result?.citations ?? []).map((c) => [`${c.side}${c.number}`, c])
  );

  const picker = (label: string, value: string, onChange: (v: string) => void, other: string) => (
    <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
      {label}
      <select className="input py-2 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t("compare.pick")}</option>
        {documents.map((d) => (
          <option key={d.id} value={d.id} disabled={d.id === other}>
            {d.filename}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="space-y-5">
      <div className="glass space-y-3 p-4">
        <div>
          <h2 className="text-sm font-semibold">{t("compare.title")}</h2>
          <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
            {t("compare.intro")}
          </p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          {picker(t("compare.first"), first, setFirst, second)}
          <button
            type="button"
            className="btn-ghost self-center sm:self-end"
            onClick={() => {
              setFirst(second);
              setSecond(first);
            }}
            aria-label="Swap the documents"
          >
            <ArrowLeftRight size={14} />
          </button>
          {picker(t("compare.second"), second, setSecond, first)}
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex flex-1 flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
            {t("compare.focus")}
            <input
              className="input py-2 text-sm"
              value={focus}
              maxLength={300}
              placeholder={t("compare.focusPlaceholder")}
              onChange={(e) => setFocus(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
          </label>
          <button
            type="button"
            className="btn-primary text-sm"
            onClick={run}
            disabled={running || !first || !second || first === second}
          >
            {running ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
            {running ? t("compare.running") : t("compare.run")}
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-xl px-4 py-2.5 text-sm" style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}>
          {error}
        </div>
      )}

      {result && (
        <section aria-live="polite" className="space-y-3">
          <div className="glass p-4">
            <h3 className="text-xs font-semibold" style={{ color: "var(--text-muted)" }}>
              {t("compare.summary")}
            </h3>
            <p className="mt-1 text-sm">{result.summary}</p>
            <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
              {t(`compare.method.${result.alignment}`)} ·{" "}
              {t("compare.stats", { pairs: result.pairs ?? 0, identical: result.identical ?? 0 })} · {result.model}
            </p>
          </div>

          {result.differences.length === 0 ? (
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              {t("compare.noDifferences")}
            </p>
          ) : (
            <ol className="space-y-2">
              {result.differences.map((difference, index) => {
                const { icon: Icon, color } = KIND_STYLE[difference.kind];
                const refs = [
                  ...(difference.a ?? []).map((n) => `a${n}`),
                  ...(difference.b ?? []).map((n) => `b${n}`),
                ];
                return (
                  <li key={index} className="glass p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
                        style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}
                      >
                        <Icon size={12} aria-hidden="true" />
                        {t(`compare.kind.${difference.kind}`)}
                      </span>
                      <span className="text-sm font-medium">{difference.topic}</span>
                    </div>
                    {difference.detail && (
                      <p className="mt-1.5 text-sm" style={{ color: "var(--text-secondary)" }}>
                        {difference.detail}
                      </p>
                    )}
                    {refs.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                        {refs.map((ref) => {
                          const citation = byLabel.get(ref);
                          if (!citation) return null;
                          const key = `${index}-${ref}`;
                          return (
                            <button
                              key={key}
                              type="button"
                              className="pill-info"
                              aria-expanded={openPassage === key}
                              onClick={() => setOpenPassage((c) => (c === key ? null : key))}
                              title={citation.filename}
                            >
                              {citation.label}
                            </button>
                          );
                        })}
                      </div>
                    )}
                    {refs.map((ref) => {
                      const citation = byLabel.get(ref);
                      if (!citation || openPassage !== `${index}-${ref}`) return null;
                      return (
                        <blockquote
                          key={ref}
                          className="mt-2 rounded-lg p-3 text-xs"
                          style={{ background: "var(--surface-1)", color: "var(--text-secondary)" }}
                        >
                          <span className="font-medium" style={{ color: "var(--accent-hover)" }}>
                            {citation.label} · {citation.filename}
                            {citation.page_number ? ` · page ${citation.page_number}` : ""}
                          </span>
                          <p className="mt-1 leading-relaxed">{citation.snippet}</p>
                        </blockquote>
                      );
                    })}
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      )}
    </div>
  );
}
