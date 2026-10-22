/**
 * SearchPanel.tsx
 * ===============
 * Semantic search with no AI answer — just the matching passages, ranked.
 *
 * WHY THIS EXISTS ALONGSIDE CHAT
 * ------------------------------
 * It shows you exactly what the retrieval step found, which is valuable twice
 * over:
 *
 *   * As a feature: sometimes you want the source passage itself, not a
 *     summary of it. This is faster, since no LLM is involved at all.
 *
 *   * As a window into the machine: the similarity score on each result makes
 *     the retrieval visible. You can watch "time off" match a section titled
 *     "Annual Leave Entitlement" at 37%, and see an unrelated query return
 *     nothing at all because everything fell below the score threshold.
 */

"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ExternalLink, FileText, Loader2, Search, SearchX } from "lucide-react";
import { useState } from "react";

import DocumentFilter from "@/components/DocumentFilter";
import SourceViewer, { SourceRef } from "@/components/SourceViewer";
import { DocumentItem, SearchResultItem, api, getErrorMessage } from "@/lib/api";

interface Props {
  projectId: string;
  /** The project's searchable documents, for the "only these files" filter. */
  documents?: DocumentItem[];
}

/** What the badge says about which half of hybrid search found a result. */
const MATCH_LABELS: Record<string, string> = {
  both: "meaning + words",
  vector: "meaning",
  keyword: "exact words",
};

export default function SearchPanel({ projectId, documents = [] }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // How long the backend took, and whether a search has run at all. The
  // `searched` flag distinguishes "no results" from "you have not searched
  // yet" — two states that should look completely different to the user.
  // "Only these documents"; empty = the whole project.
  const [documentIds, setDocumentIds] = useState<string[]>([]);
  // The result open in the source viewer.
  const [viewing, setViewing] = useState<SourceRef | null>(null);

  const [tookSeconds, setTookSeconds] = useState<number | null>(null);
  const [searched, setSearched] = useState(false);

  async function runSearch() {
    const trimmed = query.trim();
    if (!trimmed) return;

    setLoading(true);
    setError("");

    try {
      // Ignore filter entries for documents no longer listed (deleted, or
      // being re-indexed), and send the filter only when it is in use.
      const ids = documentIds.filter((id) => documents.some((d) => d.id === id));
      const response =
        ids.length > 0
          ? await api.search(trimmed, projectId, 10, ids)
          : await api.search(trimmed, projectId, 10);
      setResults(response.results);
      setTookSeconds(response.took_seconds);
      setSearched(true);
    } catch (err) {
      setError(getErrorMessage(err, "Search failed."));
    } finally {
      setLoading(false);
    }
  }

  /**
   * Pick a colour for the score badge.
   * Turning a raw number into a colour lets the user judge match quality at a
   * glance, without having to reason about what 0.37 means.
   */
  function scoreClass(score: number): string {
    if (score >= 0.5) return "pill-success";
    if (score >= 0.3) return "pill-info";
    return "pill-warning";
  }

  return (
    <div>
      {/* ---- Search box ---- */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search
            size={16}
            className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
            style={{ color: "var(--text-muted)" }}
          />
          <input
            className="input pl-11"
            placeholder="Search by meaning, e.g. “how much time off do I get?”"
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
          Search
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          This matches meaning AND exact words — a paraphrase finds the passage,
          and so does a policy number or a name.
        </p>
        <DocumentFilter documents={documents} selected={documentIds} onChange={setDocumentIds} />
      </div>

      {/* A 429 (rate limit, or the monthly token quota for chat) arrives
          here already worded with WHEN to try again: getErrorMessage in
          lib/api.ts appends the Retry-After wait when the backend's text does
          not include it. role="alert" so a screen reader announces it. */}
      {error && (
        <div
          role="alert"
          className="mt-4 rounded-xl px-4 py-3 text-sm"
          style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
        >
          {error}
        </div>
      )}

      {/* ---- Result count and timing ---- */}
      {searched && !loading && (
        <div
          className="mt-5 flex items-center gap-3 text-xs"
          style={{ color: "var(--text-muted)" }}
        >
          <span>
            {results.length} result{results.length === 1 ? "" : "s"}
          </span>
          {tookSeconds !== null && (
            <span>· found in {(tookSeconds * 1000).toFixed(0)}ms</span>
          )}
        </div>
      )}

      {/* ---- Results ---- */}
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
                      page {result.page_number}
                    </span>
                  )}
                </div>
                <span className="flex shrink-0 items-center gap-2">
                  <span className={scoreClass(result.score)}>
                    {Math.round(result.score * 100)}% match
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
                    aria-label={`View ${result.filename} in context`}
                  >
                    <ExternalLink size={12} /> View
                  </button>
                </span>
              </div>

              <p
                className="text-sm leading-relaxed"
                style={{ color: "var(--text-secondary)" }}
              >
                {/* Truncate long chunks so one result cannot dominate the page. */}
                {result.content.length > 500
                  ? `${result.content.slice(0, 500)}...`
                  : result.content}
              </p>

              {/* Chunk-level detail, useful for understanding how the document
                  was split up. */}
              <div
                className="mt-2 flex gap-3 text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                <span>chunk #{result.chunk_index}</span>
                {result.match_type && (
                  <span title="Which half of hybrid search found this passage">
                    found by {MATCH_LABELS[result.match_type] ?? result.match_type}
                  </span>
                )}
                {result.meta_data?.token_count && (
                  <span>{result.meta_data.token_count} tokens</span>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {/* ---- Empty state ----
            Only shown AFTER a search has actually run, so the panel does not
            greet a first-time user with "no results found". */}
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
            <h3 className="font-semibold">Nothing matched closely enough</h3>
            <p
              className="mt-2 max-w-sm text-sm"
              style={{ color: "var(--text-secondary)" }}
            >
              Every passage scored below the similarity threshold, so nothing is
              shown rather than presenting a weak match as an answer. Try
              rephrasing, or check that documents in this project have finished
              processing.
            </p>
          </motion.div>
        )}
      </div>
      {viewing && <SourceViewer source={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
