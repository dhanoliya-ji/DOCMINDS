// Semantic search with no AI answer — just the matching passages, ranked.

"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ExternalLink, FileText, Loader2, Search, SearchX } from "lucide-react";
import { useMemo, useState } from "react";

import DocumentFilter from "@/components/DocumentFilter";
import EmbeddingCloud from "@/components/EmbeddingCloud";
import SourceViewer, { SourceRef } from "@/components/SourceViewer";
import { DocumentItem, SearchResultItem, api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Props {
  projectId: string;
  documents?: DocumentItem[];
}

const MATCH_TYPES = new Set(["both", "vector", "keyword"]);

export default function SearchPanel({ projectId, documents = [] }: Props) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [documentIds, setDocumentIds] = useState<string[]>([]);
  const [viewing, setViewing] = useState<SourceRef | null>(null);

  const [tookSeconds, setTookSeconds] = useState<number | null>(null);
  const [searched, setSearched] = useState(false);
  const hitIds = useMemo(() => results.map((r) => r.chunk_id), [results]);

  async function runSearch() {
    const trimmed = query.trim();
    if (!trimmed) return;

    setLoading(true);
    setError("");

    try {
      const ids = documentIds.filter((id) => documents.some((d) => d.id === id));
      const response =
        ids.length > 0
          ? await api.search(trimmed, projectId, 10, ids)
          : await api.search(trimmed, projectId, 10);
      setResults(response.results);
      setTookSeconds(response.took_seconds);
      setSearched(true);
    } catch (err) {
      setError(getErrorMessage(err, t("search.failed")));
    } finally {
      setLoading(false);
    }
  }

  function scoreClass(score: number): string {
    if (score >= 0.5) return "pill-success";
    if (score >= 0.3) return "pill-info";
    return "pill-warning";
  }

  return (
    <div>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search
            size={16}
            className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
            style={{ color: "var(--text-muted)" }}
          />
          <input
            className="input pl-11"
            placeholder={t("search.placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
        </div>
        <button
          onClick={runSearch}
          disabled={loading || !query.trim()}
          className="btn-primary shrink-0"
        >
          {loading ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Search size={16} />
          )}
          {t("search.button")}
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {t("search.hint")}
        </p>
        <DocumentFilter documents={documents} selected={documentIds} onChange={setDocumentIds} />
      </div>

      {error && (
        <div
          role="alert"
          className="mt-4 rounded-xl px-4 py-3 text-sm"
          style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
        >
          {error}
        </div>
      )}

      {searched && !loading && (
        <div
          className="mt-5 flex items-center gap-3 text-xs"
          style={{ color: "var(--text-muted)" }}
        >
          <span>
            {t(results.length === 1 ? "search.results.one" : "search.results.other", { count: results.length })}
          </span>
          {tookSeconds !== null && (
            <span>{t("search.took", { ms: (tookSeconds * 1000).toFixed(0) })}</span>
          )}
        </div>
      )}

      <div className="mt-3 space-y-3">
        <AnimatePresence mode="popLayout">
          {results.map((result, index) => (
            <motion.div
              key={result.chunk_id}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ delay: index * 0.04, duration: 0.3 }}
              className="glass glass-hover p-4"
            >
              <div className="mb-2 flex items-start justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <FileText size={14} style={{ color: "var(--accent-hover)" }} />
                  <span>{result.filename}</span>
                  {result.page_number && (
                    <span
                      className="text-xs"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {t("search.page", { page: result.page_number })}
                    </span>
                  )}
                </div>
                <span className="flex shrink-0 items-center gap-2">
                  <span className={scoreClass(result.score)}>
                    {t("search.match", { percent: Math.round(result.score * 100) })}
                  </span>
                  <button
                    type="button"
                    className="flex items-center gap-1 text-xs"
                    style={{ color: "var(--accent-hover)" }}
                    onClick={() =>
                      setViewing({
                        document_id: result.document_id,
                        chunk_id: result.chunk_id,
                        filename: result.filename,
                        page_number: result.page_number,
                        snippet: result.content,
                      })
                    }
                    aria-label={t("search.viewLabel", { filename: result.filename })}
                  >
                    <ExternalLink size={12} /> {t("search.view")}
                  </button>
                </span>
              </div>

              <p
                className="text-sm leading-relaxed"
                style={{ color: "var(--text-secondary)" }}
              >
                {result.content.length > 500
                  ? `${result.content.slice(0, 500)}...`
                  : result.content}
              </p>

              <div
                className="mt-2 flex gap-3 text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                <span>{t("search.chunk", { index: result.chunk_index })}</span>
                {result.match_type && (
                  <span title={t("search.foundByTitle")}>
                    {t("search.foundBy", {
                      how: MATCH_TYPES.has(result.match_type)
                        ? t(`search.match.${result.match_type}`)
                        : result.match_type,
                    })}
                  </span>
                )}
                {result.meta_data?.token_count && (
                  <span>{t("search.tokens", { count: result.meta_data.token_count })}</span>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {searched && !loading && results.length === 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="glass flex flex-col items-center px-6 py-14 text-center"
          >
            <SearchX
              size={32}
              style={{ color: "var(--text-muted)" }}
              className="mb-3"
            />
            <h3 className="font-semibold">{t("search.empty.title")}</h3>
            <p
              className="mt-2 max-w-sm text-sm"
              style={{ color: "var(--text-secondary)" }}
            >
              {t("search.empty.body")}
            </p>
          </motion.div>
        )}
      </div>
      <EmbeddingCloud projectId={projectId} hitIds={hitIds} />
      {viewing && <SourceViewer source={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
