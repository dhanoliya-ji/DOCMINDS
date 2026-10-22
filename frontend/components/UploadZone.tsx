/**
 * UploadZone.tsx
 * ==============
 * The drag-and-drop upload area -- for one file or fifty.
 *
 * WHAT HAPPENS WHEN YOU DROP FILES
 * --------------------------------
 *   1. Every file gets its own row: name, size, a progress bar.
 *   2. Files are sent ONE PER REQUEST, at most three at a time, to
 *      POST /api/v1/documents/upload, with the bytes-sent progress of each
 *      shown as it goes (api.uploadFile uses XMLHttpRequest for that --
 *      fetch cannot report upload progress).
 *   3. The backend stores each file, creates a Document row with status
 *      "pending", queues it for processing, and replies IMMEDIATELY.
 *   4. The parent page then polls until the status becomes "completed".
 *
 * WHY ONE REQUEST PER FILE
 * ------------------------
 * The upload endpoint validates a whole request together and refuses all of
 * it if any file is bad -- the right rule for an API call, the wrong one for
 * someone dragging in a folder of forty PDFs where one is a renamed .exe.
 * Sent separately, the bad file becomes one red row with its reason
 * ("Unsupported file: ...") and the other thirty-nine go through.
 *
 * Three at a time keeps the browser's connection pool and the upload rate
 * limit (RATE_LIMIT_UPLOAD) comfortable while still finishing a batch fast.
 *
 * HOW HTML DRAG AND DROP WORKS
 * ----------------------------
 * A browser's default reaction to a file dropped on a page is to NAVIGATE to
 * that file. To build a drop zone you must call `preventDefault()` on both
 * `onDragOver` and `onDrop` — that is what tells the browser "I am handling
 * this myself". Forgetting the `onDragOver` one is the classic reason a drop
 * zone appears to do nothing.
 */

"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, Loader2, UploadCloud, X, XCircle } from "lucide-react";
import { DragEvent, useRef, useState } from "react";

import { ApiError, api, getErrorMessage } from "@/lib/api";

/**
 * Word an upload failure for the person who just dropped the files.
 *
 * The backend's `detail` already says WHAT was wrong ("handbook.exe: this file
 * type is not supported"); the prefix adds what kind of problem it is, so a
 * user can tell "make it smaller" from "convert it" from "wait" at a glance.
 * `instanceof ApiError` is checked by status rather than trusted blindly: an
 * error from anywhere else (a network failure) falls through to its message.
 */
export function uploadErrorMessage(error: unknown): string {
  const detail = getErrorMessage(error, "Upload failed.");
  if (!(error instanceof ApiError)) return detail;
  switch (error.status) {
    case 413:
      return `File too large: ${detail}`;
    case 415:
      return `Unsupported file: ${detail}`;
    case 429:
      // getErrorMessage has already appended "Try again in ..." if needed.
      return `Too many uploads: ${detail}`;
    case 402:
      return `Quota reached: ${detail}`;
    case 503:
      return `Upload unavailable: ${detail}`;
    default:
      return detail;
  }
}

/** Formats accepted by the backend extractor, for the file picker's filter. */
const ACCEPTED =
  ".pdf,.docx,.pptx,.xlsx,.xls,.html,.htm,.eml,.txt,.md,.markdown,.csv,.zip,.png,.jpg,.jpeg,.tiff,.bmp,.gif";

/** How many files are in flight at once. */
const CONCURRENCY = 3;

/** One file's journey through the upload. */
export interface UploadRow {
  key: string;
  name: string;
  size: number;
  status: "queued" | "uploading" | "done" | "error";
  // 0..1, bytes sent so far.
  progress: number;
  // The error, worded for the user, when status is "error".
  message?: string;
}

/** Turn a byte count into something human, e.g. 1261 -> "1.2 KB". */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Run `worker` over `items` with at most `limit` running at once.
 *
 * A tiny pool: `limit` loops each take the next unclaimed item until none
 * are left. Exported for the tests.
 */
export async function runPool<T>(items: T[], limit: number, worker: (item: T) => Promise<void>) {
  let next = 0;
  const lanes = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const item = items[next++];
      await worker(item);
    }
  });
  await Promise.all(lanes);
}

interface Props {
  projectId: string;
  /** Called after each successful upload so the parent can refresh its list. */
  onUploaded: () => void;
}

export default function UploadZone({ projectId, onUploaded }: Props) {
  // True while a file is being dragged over the zone, used to highlight it.
  const [dragging, setDragging] = useState(false);
  const [rows, setRows] = useState<UploadRow[]>([]);
  const nextKey = useRef(0);

  // A `ref` holds a direct handle on a DOM element. We need one here because
  // the native file input is ugly and cannot be styled, so we hide it and
  // trigger it programmatically when our own styled button is clicked.
  const fileInputRef = useRef<HTMLInputElement>(null);

  const busy = rows.some((r) => r.status === "queued" || r.status === "uploading");

  function update(key: string, change: Partial<UploadRow>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...change } : row)));
  }

  async function upload(files: File[]) {
    if (files.length === 0) return;

    const batch = files.map((file) => ({
      file,
      row: {
        key: `upload-${nextKey.current++}`,
        name: file.name,
        size: file.size,
        status: "queued" as const,
        progress: 0,
      },
    }));
    // A new batch replaces the finished rows of the last one but keeps its
    // failures until they are dismissed -- a stale success next to a new
    // upload is noise, a stale error is still news.
    setRows((current) => [...current.filter((r) => r.status === "error"), ...batch.map((b) => b.row)]);

    await runPool(batch, CONCURRENCY, async ({ file, row }) => {
      update(row.key, { status: "uploading" });
      try {
        await api.uploadFile(projectId, file, (fraction) => update(row.key, { progress: fraction }));
        update(row.key, { status: "done", progress: 1 });
        // Tell the parent to reload, so the new document appears in the list
        // with its live "processing" status straight away.
        onUploaded();
      } catch (err) {
        update(row.key, { status: "error", message: uploadErrorMessage(err) });
      }
    });

    // Clear the successful rows after a few seconds so they do not linger;
    // failures stay until dismissed.
    const keys = new Set(batch.map((b) => b.row.key));
    setTimeout(
      () => setRows((current) => current.filter((r) => !(keys.has(r.key) && r.status === "done"))),
      4000
    );
  }

  // ---- Drag handlers -------------------------------------------------

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    // Without this the browser will not fire `onDrop` at all.
    event.preventDefault();
    setDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);

    // `dataTransfer.files` is a FileList, an array-LIKE object that has no
    // .map or .filter. `Array.from` converts it into a real array.
    upload(Array.from(event.dataTransfer.files));
  }

  const done = rows.filter((r) => r.status === "done").length;
  const failed = rows.filter((r) => r.status === "error").length;

  return (
    <div>
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        // Clicking anywhere in the zone opens the system file picker, by
        // forwarding the click to the hidden input.
        onClick={() => fileInputRef.current?.click()}
        className="glass glass-hover flex cursor-pointer flex-col items-center justify-center px-6 py-10 text-center transition-all"
        style={{
          // Highlight the zone while a file hovers over it.
          borderColor: dragging ? "var(--accent)" : "var(--border-subtle)",
          background: dragging ? "rgba(99,102,241,0.08)" : undefined,
        }}
      >
        <p className="font-medium">
          {busy ? "Uploading..." : dragging ? "Drop to upload" : "Drag files here, or click to browse"}
        </p>
        <motion.div
          // The icon lifts slightly while dragging, reinforcing the highlight.
          animate={{ scale: dragging ? 1.08 : 1, y: dragging ? -3 : 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
          className="order-first mb-3 inline-flex h-12 w-12 items-center justify-center rounded-xl"
          style={{ background: "rgba(99,102,241,0.14)", color: "var(--accent-hover)" }}
        >
          {busy ? <Loader2 size={22} className="animate-spin" /> : <UploadCloud size={22} />}
        </motion.div>
        <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
          PDF · DOCX · PPTX · XLSX · HTML · EML · CSV · TXT · MD · ZIP · images — many at once
        </p>
        <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          Scanned PDFs are detected automatically and sent through OCR.
        </p>

        {/* The real file input, hidden. `multiple` allows selecting several files. */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED}
          className="hidden"
          data-testid="file-input"
          onChange={(e) => {
            upload(Array.from(e.target.files || []));
            // Reset the input's value. Without this, selecting the SAME file
            // twice in a row fires no change event the second time.
            e.target.value = "";
          }}
        />
      </div>

      {/* ---- Per-file progress ---- */}
      <AnimatePresence>
        {rows.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="glass mt-3 p-3"
          >
            <div className="mb-2 flex items-center justify-between text-xs" style={{ color: "var(--text-muted)" }}>
              <span aria-live="polite">
                {done} of {rows.length} uploaded
                {failed > 0 ? ` · ${failed} failed` : ""}
              </span>
              {!busy && failed > 0 && (
                <button
                  type="button"
                  className="flex items-center gap-1"
                  onClick={() => setRows((current) => current.filter((r) => r.status !== "error"))}
                >
                  <X size={12} /> Clear errors
                </button>
              )}
            </div>
            <ul className="space-y-2" aria-label="Uploads">
              {rows.map((row) => (
                <li key={row.key} className="text-xs">
                  <div className="flex items-center gap-2">
                    {row.status === "done" ? (
                      <CheckCircle2 size={13} style={{ color: "var(--success)" }} aria-label="Uploaded" />
                    ) : row.status === "error" ? (
                      <XCircle size={13} style={{ color: "var(--danger)" }} aria-label="Failed" />
                    ) : (
                      <Loader2 size={13} className="animate-spin" aria-label="Uploading" />
                    )}
                    <span className="min-w-0 flex-1 truncate">{row.name}</span>
                    <span style={{ color: "var(--text-muted)" }}>{formatSize(row.size)}</span>
                    <span className="w-10 text-right" style={{ color: "var(--text-muted)" }}>
                      {row.status === "queued" ? "queued" : `${Math.round(row.progress * 100)}%`}
                    </span>
                  </div>
                  {(row.status === "uploading" || row.status === "queued") && (
                    <div
                      className="mt-1 h-1 overflow-hidden rounded-full"
                      style={{ background: "rgba(255,255,255,0.06)" }}
                      role="progressbar"
                      aria-label={`Uploading ${row.name}`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(row.progress * 100)}
                    >
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${Math.round(row.progress * 100)}%`,
                          background: "linear-gradient(90deg, var(--accent), var(--violet))",
                        }}
                      />
                    </div>
                  )}
                  {row.status === "error" && (
                    // role="alert": announced immediately, because the upload
                    // the user was waiting on did not happen.
                    <p role="alert" className="mt-0.5 pl-5" style={{ color: "var(--danger)" }}>
                      {row.message}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
