/**
 * SourceViewer.tsx
 * ================
 * Opens the document a citation came from, at the cited place, with the
 * cited passage highlighted. This is what makes "[2]" checkable in one click
 * instead of "go and find page 12 of handbook.pdf yourself".
 *
 * TWO WAYS TO SHOW A SOURCE
 * -------------------------
 *   PDF        the ORIGINAL page, drawn by pdf.js (lib/pdf.ts) from the bytes
 *              of GET /documents/{id}/preview, with the passage's lines
 *              painted over in yellow. Prev / next move through the file.
 *
 *   anything   DOCX, spreadsheets, emails, slides... a browser cannot draw
 *   else       those, so we show the EXTRACTED TEXT instead: the cited chunk
 *              and its neighbours (GET /documents/{id}/chunks?around=), with
 *              the snippet marked. It is also the fallback when a PDF cannot
 *              be rendered, and a "Show text" toggle offers it for PDFs too.
 *
 * ACCESSIBILITY
 * -------------
 * A modal dialog: role="dialog" with aria-modal and a label, focus moved into
 * it on open and back to whatever opened it on close, and Escape closes it.
 */

"use client";

import { ChevronLeft, ChevronRight, FileText, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ChunkContext, api, getErrorMessage } from "@/lib/api";
import { findPassage } from "@/lib/highlight";
import type { PdfHandle } from "@/lib/pdf";

/** What the viewer needs to know about a source -- a citation or a search hit. */
export interface SourceRef {
  document_id: string;
  chunk_id: string;
  filename: string;
  page_number: number | null;
  // The passage to highlight: a citation's snippet or a search hit's text.
  snippet: string;
  number?: number;
}

interface Props {
  source: SourceRef;
  onClose: () => void;
}

/** Can a browser draw this file itself? Decided by the stored extension. */
export function isPdf(filename: string): boolean {
  return /\.pdf$/i.test(filename || "");
}

export default function SourceViewer({ source, onClose }: Props) {
  const [view, setView] = useState<"pdf" | "text">(isPdf(source.filename) ? "pdf" : "text");
  const dialogRef = useRef<HTMLDivElement>(null);

  // Escape closes; focus moves in on open and back out on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(3,6,15,0.72)" }}
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Source: ${source.filename}`}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="glass flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden outline-none"
        style={{ background: "var(--bg-elevated)" }}
      >
        <header
          className="flex items-center justify-between gap-3 border-b px-4 py-3"
          style={{ borderColor: "var(--border-subtle)" }}
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2 truncate text-sm font-medium">
              <FileText size={14} aria-hidden="true" />
              {source.number ? `[${source.number}] ` : ""}
              {source.filename}
            </div>
            {source.page_number ? (
              <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                Cited on page {source.page_number}
              </div>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            {isPdf(source.filename) && (
              <button
                type="button"
                className="btn-ghost text-xs"
                onClick={() => setView(view === "pdf" ? "text" : "pdf")}
              >
                {view === "pdf" ? "Show text" : "Show page"}
              </button>
            )}
            <button type="button" className="btn-ghost" onClick={onClose} aria-label="Close source">
              <X size={16} />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-auto p-4">
          {view === "pdf" ? (
            <PdfView source={source} onUnavailable={() => setView("text")} />
          ) : (
            <TextView source={source} />
          )}
        </div>
      </div>
    </div>
  );
}

// ======================================================================
// PDF: the original page, highlighted
// ======================================================================

function PdfView({ source, onUnavailable }: { source: SourceRef; onUnavailable: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PdfHandle | null>(null);
  const [page, setPage] = useState(source.page_number || 1);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [highlighted, setHighlighted] = useState(0);

  // Fetch and open the file once.
  useEffect(() => {
    let cancelled = false;
    let handle: PdfHandle | null = null;
    (async () => {
      try {
        const bytes = await api.fetchDocumentFile(source.document_id);
        // Imported here so pdf.js is only downloaded when a PDF is opened.
        const { openPdf } = await import("@/lib/pdf");
        handle = await openPdf(bytes);
        if (cancelled) {
          handle.destroy();
          return;
        }
        setPdf(handle);
      } catch (err) {
        if (cancelled) return;
        setError(getErrorMessage(err, "Could not open the PDF."));
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
      handle?.destroy();
    };
  }, [source.document_id]);

  // Draw the current page whenever it (or the document) changes.
  useEffect(() => {
    if (!pdf || !canvasRef.current) return;
    let cancelled = false;
    // Highlight only on the cited page: the snippet belongs there.
    const snippet = page === (source.page_number || 1) ? source.snippet : undefined;
    pdf
      .renderPage(page, canvasRef.current, overlayRef.current, snippet)
      .then((count) => {
        if (cancelled) return;
        setHighlighted(count);
        setStatus("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(getErrorMessage(err, "Could not draw this page."));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [pdf, page, source.page_number, source.snippet]);

  if (status === "error") {
    return (
      <div className="space-y-3 text-sm">
        <p role="alert" style={{ color: "var(--danger)" }}>
          {error}
        </p>
        <button type="button" className="btn-ghost text-xs" onClick={onUnavailable}>
          Show the extracted text instead
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {pdf && (
        <div className="flex items-center justify-between text-xs" style={{ color: "var(--text-muted)" }}>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn-ghost px-2!"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              aria-label="Previous page"
            >
              <ChevronLeft size={14} />
            </button>
            <span>
              Page {page} of {pdf.numPages}
            </span>
            <button
              type="button"
              className="btn-ghost px-2!"
              onClick={() => setPage((p) => Math.min(pdf.numPages, p + 1))}
              disabled={page >= pdf.numPages}
              aria-label="Next page"
            >
              <ChevronRight size={14} />
            </button>
          </div>
          {page === (source.page_number || 1) && status === "ready" && (
            <span>
              {highlighted > 0
                ? "Cited passage highlighted"
                : "Passage not located on the page -- try Show text"}
            </span>
          )}
        </div>
      )}
      {status === "loading" && (
        <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
          <Loader2 size={14} className="animate-spin" /> Opening the document...
        </div>
      )}
      <div className="relative mx-auto w-fit">
        <canvas ref={canvasRef} className="block max-w-full" aria-label={`Page ${page}`} />
        <canvas
          ref={overlayRef}
          className="pointer-events-none absolute inset-0 block max-w-full"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}

// ======================================================================
// Everything else: the extracted text, highlighted
// ======================================================================

function TextView({ source }: { source: SourceRef }) {
  const [chunks, setChunks] = useState<ChunkContext[] | null>(null);
  const [error, setError] = useState("");
  const citedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .documentChunks(source.document_id, source.chunk_id, 2)
      .then((rows) => !cancelled && setChunks(rows))
      .catch((err) => !cancelled && setError(getErrorMessage(err, "Could not load the text.")));
    return () => {
      cancelled = true;
    };
  }, [source.document_id, source.chunk_id]);

  // Bring the cited chunk into view once it is on screen.
  useEffect(() => {
    citedRef.current?.scrollIntoView?.({ block: "center" });
  }, [chunks]);

  if (error) {
    return (
      <p role="alert" className="text-sm" style={{ color: "var(--danger)" }}>
        {error}
      </p>
    );
  }
  if (!chunks) return <div className="glass shimmer h-40" aria-busy="true" />;

  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {chunks.map((chunk) => {
        const cited = chunk.id === source.chunk_id;
        return (
          <div
            key={chunk.id}
            ref={cited ? citedRef : undefined}
            className="rounded-lg p-3"
            style={{
              background: cited ? "rgba(99,102,241,0.08)" : "transparent",
              color: cited ? "var(--text-primary)" : "var(--text-secondary)",
            }}
            data-cited={cited ? "true" : undefined}
          >
            {chunk.page_number ? (
              <div className="mb-1 text-xs" style={{ color: "var(--text-muted)" }}>
                Page {chunk.page_number}
              </div>
            ) : null}
            <p className="whitespace-pre-wrap">
              {cited ? <Highlighted text={chunk.content} passage={source.snippet} /> : chunk.content}
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** `text` with the part matching `passage` wrapped in <mark>. */
export function Highlighted({ text, passage }: { text: string; passage: string }) {
  const range = findPassage(text, passage);
  if (!range) return <>{text}</>;
  const [start, end] = range;
  return (
    <>
      {text.slice(0, start)}
      <mark style={{ background: "rgba(250,204,21,0.35)", color: "inherit", borderRadius: 3 }}>
        {text.slice(start, end)}
      </mark>
      {text.slice(end)}
    </>
  );
}
