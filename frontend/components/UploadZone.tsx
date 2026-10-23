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
 * THE ZONE'S STATES (F11)
 * ------------------------
 * The drop area itself animates between its states, so you can tell from
 * across the room what it is doing:
 *
 *     idle -> drag-over -> uploading -> processing -> done | error
 *
 *   drag-over    the dashed border takes the accent colour, the icon lifts
 *   uploading    an arrow climbs on a loop inside a ring that fills with
 *                the batch's overall progress
 *   processing   every byte is sent but the server has not replied yet (it
 *                is storing, scanning and queueing) -- the ring pulses
 *   done / error a tick or a cross pops in, until the rows clear
 *
 * Each row keeps its own bar and percentage, as before. Under
 * prefers-reduced-motion nothing moves: the same icons and colours appear
 * with every transition set to zero.
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
import { ArrowUp, Check, CheckCircle2, Loader2, UploadCloud, X, XCircle } from "lucide-react";
import { DragEvent, useRef, useState } from "react";

import { usePrefersReducedMotion } from "@/components/landing/hooks";
import { ApiError, api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

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

/** What the drop area as a whole is showing (F11). */
export type ZonePhase = "idle" | "drag-over" | "uploading" | "processing" | "done" | "error";

/**
 * Work out the zone's phase from the rows. Exported for the tests.
 *
 * A row whose bytes are all sent (progress 1) but whose request has not yet
 * returned is "processing": the server is storing and scanning it.
 */
export function zonePhase(dragging: boolean, rows: UploadRow[]): ZonePhase {
  if (dragging) return "drag-over";
  const active = rows.filter((r) => r.status === "queued" || r.status === "uploading");
  if (active.length > 0) {
    const sending = active.some((r) => r.status === "queued" || r.progress < 1);
    return sending ? "uploading" : "processing";
  }
  if (rows.some((r) => r.status === "error")) return "error";
  if (rows.some((r) => r.status === "done")) return "done";
  return "idle";
}

/** The headline for each phase (an i18n key). */
const PHASE_TEXT: Record<ZonePhase, string> = {
  idle: "upload.idle",
  "drag-over": "upload.dragOver",
  uploading: "upload.uploading",
  processing: "upload.processing",
  done: "upload.done",
  error: "upload.error",
};

/** Colour per phase, as theme tokens so light and dark both work. */
const PHASE_COLOR: Record<ZonePhase, string> = {
  idle: "var(--accent-hover)",
  "drag-over": "var(--accent)",
  uploading: "var(--accent)",
  processing: "var(--violet)",
  done: "var(--success)",
  error: "var(--danger)",
};

/**
 * The animated icon in the middle of the zone.
 *
 * An SVG ring around a lucide icon: the ring's `pathLength` follows the
 * batch's overall progress while uploading, and pulses while processing.
 * Icons swap through AnimatePresence so each state pops in rather than
 * cutting. `reduced` makes every transition instant.
 */
function ZoneIcon({ phase, progress, reduced }: { phase: ZonePhase; progress: number; reduced: boolean }) {
  const color = PHASE_COLOR[phase];
  const still = { duration: 0 };
  const icon =
    phase === "done" ? (
      <Check size={22} />
    ) : phase === "error" ? (
      <X size={22} />
    ) : phase === "uploading" ? (
      <motion.span
        className="inline-flex"
        // The arrow climbs and fades on a loop: "going up".
        animate={reduced ? undefined : { y: [4, -4], opacity: [0.4, 1, 0.4] }}
        transition={reduced ? still : { duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
      >
        <ArrowUp size={22} />
      </motion.span>
    ) : phase === "processing" ? (
      <Loader2 size={22} className={reduced ? undefined : "animate-spin"} />
    ) : (
      <UploadCloud size={22} />
    );

  return (
    <motion.div
      data-testid="upload-zone-icon"
      data-phase={phase}
      // Lifts while a file hovers over the zone, reinforcing the highlight.
      animate={{ scale: phase === "drag-over" ? 1.1 : 1, y: phase === "drag-over" ? -4 : 0 }}
      transition={reduced ? still : { type: "spring", stiffness: 300, damping: 20 }}
      className="relative order-first mb-3 inline-flex h-14 w-14 items-center justify-center rounded-2xl"
      style={{ background: "var(--surface-2)", color }}
      aria-hidden="true"
    >
      <svg className="absolute inset-0 h-full w-full -rotate-90" viewBox="0 0 56 56" fill="none">
        {(phase === "uploading" || phase === "processing") && (
          <motion.circle
            cx="28"
            cy="28"
            r="25"
            stroke={color}
            strokeWidth="2.5"
            strokeLinecap="round"
            initial={false}
            animate={
              phase === "uploading"
                ? { pathLength: Math.max(0.04, progress), opacity: 1 }
                : reduced
                  ? { pathLength: 1, opacity: 0.8 }
                  : { pathLength: 1, opacity: [0.3, 1, 0.3] }
            }
            transition={
              reduced
                ? still
                : phase === "processing"
                  ? { opacity: { duration: 1.4, repeat: Infinity }, pathLength: { duration: 0.3 } }
                  : { duration: 0.3 }
            }
          />
        )}
      </svg>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={phase === "drag-over" ? "idle" : phase}
          className="inline-flex"
          initial={reduced ? false : { scale: 0.4, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={reduced ? undefined : { scale: 0.4, opacity: 0 }}
          transition={reduced ? still : { type: "spring", stiffness: 400, damping: 22 }}
        >
          {icon}
        </motion.span>
      </AnimatePresence>
    </motion.div>
  );
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
  const { t } = useT();
  const reduced = usePrefersReducedMotion();

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
  const phase = zonePhase(dragging, rows);
  // The whole batch's progress, for the ring around the icon.
  const overall = rows.length
    ? rows.reduce((sum, r) => sum + (r.status === "error" ? 1 : r.progress), 0) / rows.length
    : 0;

  return (
    <div>
      <motion.div
        data-testid="upload-zone"
        data-phase={phase}
        role="button"
        tabIndex={0}
        aria-label={t("upload.zoneLabel")}
        // Keyboard users open the picker with Enter or Space, like any button.
        onKeyDown={(e) => {
          if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        // Clicking anywhere in the zone opens the system file picker, by
        // forwarding the click to the hidden input.
        onClick={() => fileInputRef.current?.click()}
        // The zone swells a touch while a file hovers over it.
        animate={{ scale: phase === "drag-over" && !reduced ? 1.01 : 1 }}
        transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 300, damping: 24 }}
        className="glass glass-hover flex cursor-pointer flex-col items-center justify-center px-6 py-10 text-center transition-colors"
        style={{
          // The border takes the phase's colour; dashed while waiting for a drop.
          borderColor: phase === "idle" ? "var(--border-subtle)" : PHASE_COLOR[phase],
          borderStyle: phase === "idle" || phase === "drag-over" ? "dashed" : "solid",
          background: phase === "drag-over" ? "var(--surface-2)" : undefined,
        }}
      >
        <p className="font-medium">{t(PHASE_TEXT[phase])}</p>
        <ZoneIcon phase={phase} progress={overall} reduced={reduced} />
        <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
          {t("upload.formats")}
        </p>
        <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          {t("upload.ocr")}
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
      </motion.div>

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
                {t("upload.summary", { done, total: rows.length })}
                {failed > 0 ? t("upload.failedCount", { failed }) : ""}
              </span>
              {!busy && failed > 0 && (
                <button
                  type="button"
                  className="flex items-center gap-1"
                  onClick={() => setRows((current) => current.filter((r) => r.status !== "error"))}
                >
                  <X size={12} /> {t("upload.clearErrors")}
                </button>
              )}
            </div>
            <ul className="space-y-2" aria-label={t("upload.list")}>
              {rows.map((row) => (
                <li key={row.key} className="text-xs">
                  <div className="flex items-center gap-2">
                    {row.status === "done" ? (
                      <CheckCircle2 size={13} style={{ color: "var(--success)" }} aria-label={t("upload.rowUploaded")} />
                    ) : row.status === "error" ? (
                      <XCircle size={13} style={{ color: "var(--danger)" }} aria-label={t("upload.rowFailed")} />
                    ) : (
                      <Loader2
                        size={13}
                        className={reduced ? undefined : "animate-spin"}
                        aria-label={t("upload.rowUploading")}
                      />
                    )}
                    <span className="min-w-0 flex-1 truncate">{row.name}</span>
                    <span style={{ color: "var(--text-muted)" }}>{formatSize(row.size)}</span>
                    <span className="min-w-10 text-right" style={{ color: "var(--text-muted)" }}>
                      {row.status === "queued"
                        ? t("upload.queued")
                        : row.status === "uploading" && row.progress >= 1
                          ? t("upload.rowProcessing")
                          : `${Math.round(row.progress * 100)}%`}
                    </span>
                  </div>
                  {(row.status === "uploading" || row.status === "queued") && (
                    <div
                      className="mt-1 h-1 overflow-hidden rounded-full"
                      style={{ background: "var(--surface-2)" }}
                      role="progressbar"
                      aria-label={t("upload.progressLabel", { name: row.name })}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(row.progress * 100)}
                    >
                      <div
                        className={`h-full rounded-full ${reduced ? "" : "transition-all"}`}
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
