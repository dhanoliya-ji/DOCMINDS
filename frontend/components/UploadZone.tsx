/**
 * UploadZone.tsx
 * ==============
 * The drag-and-drop upload area.
 *
 * WHAT HAPPENS WHEN YOU DROP A FILE
 * ---------------------------------
 *   1. The browser hands us a `File` object.
 *   2. We POST it to /api/v1/documents/upload as multipart form data.
 *   3. The backend saves it, creates a Document row with status "pending",
 *      queues a Celery job, and replies IMMEDIATELY.
 *   4. The parent page then polls until the status becomes "completed".
 *
 * Step 3 is the important one: the response comes back in milliseconds even
 * for a 200-page scanned PDF, because the actual processing happens later in a
 * background worker. The user is never left waiting on a spinner.
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
import { FileUp, Loader2, UploadCloud, X } from "lucide-react";
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

interface Props {
  projectId: string;
  /** Called after a successful upload so the parent can refresh its list. */
  onUploaded: () => void;
}

export default function UploadZone({ projectId, onUploaded }: Props) {
  // True while a file is being dragged over the zone, used to highlight it.
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  // Names of the files from the most recent upload, shown as confirmation.
  const [justUploaded, setJustUploaded] = useState<string[]>([]);

  // A `ref` holds a direct handle on a DOM element. We need one here because
  // the native file input is ugly and cannot be styled, so we hide it and
  // trigger it programmatically when our own styled button is clicked.
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function upload(files: File[]) {
    if (files.length === 0) return;

    setError("");
    setUploading(true);

    try {
      const created = await api.uploadDocuments(projectId, files);
      setJustUploaded(created.map((doc) => doc.filename));

      // Tell the parent to reload, so the new documents appear in the list
      // with their live "processing" status.
      onUploaded();

      // Clear the confirmation after a few seconds so it does not linger.
      setTimeout(() => setJustUploaded([]), 4000);
    } catch (err) {
      setError(uploadErrorMessage(err));
    } finally {
      setUploading(false);
    }
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
    const files = Array.from(event.dataTransfer.files);
    upload(files);
  }

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
          // Highlight the zone while a file hovers over it, so the user gets
          // clear feedback that dropping here will work.
          borderColor: dragging ? "var(--accent)" : "var(--border-subtle)",
          background: dragging ? "rgba(99,102,241,0.08)" : undefined,
        }}
      >
        <motion.div
          // The icon lifts slightly while dragging, reinforcing the highlight.
          animate={{ scale: dragging ? 1.08 : 1, y: dragging ? -3 : 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
          className="mb-3 inline-flex h-12 w-12 items-center justify-center rounded-xl"
          style={{
            background: "rgba(99,102,241,0.14)",
            color: "var(--accent-hover)",
          }}
        >
          {uploading ? (
            <Loader2 size={22} className="animate-spin" />
          ) : (
            <UploadCloud size={22} />
          )}
        </motion.div>

        <p className="font-medium">
          {uploading
            ? "Uploading..."
            : dragging
            ? "Drop to upload"
            : "Drag files here, or click to browse"}
        </p>
        <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
          PDF · DOCX · PPTX · XLSX · HTML · EML · CSV · TXT · MD · ZIP · images
        </p>
        <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          Scanned PDFs are detected automatically and sent through OCR.
        </p>

        {/* The real file input, hidden. `multiple` allows selecting several
            files at once, which the backend endpoint accepts as a list. */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED}
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            upload(files);
            // Reset the input's value. Without this, selecting the SAME file
            // twice in a row fires no change event the second time, because
            // the value has not changed — a classic and confusing bug.
            e.target.value = "";
          }}
        />
      </div>

      {/* ---- Feedback ---- */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-3 flex items-center justify-between rounded-xl px-4 py-2.5 text-sm"
            style={{
              background: "rgba(248,113,113,0.1)",
              color: "var(--danger)",
            }}
          >
            {/* role="alert": announced immediately, because the upload the
                user was waiting on did not happen. */}
            <span role="alert">{error}</span>
            <button onClick={() => setError("")} aria-label="Dismiss error">
              <X size={14} />
            </button>
          </motion.div>
        )}

        {justUploaded.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-3 flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm"
            style={{
              background: "rgba(52,211,153,0.1)",
              color: "var(--success)",
            }}
          >
            <FileUp size={14} />
            <span>
              Queued {justUploaded.length} file
              {justUploaded.length > 1 ? "s" : ""} for processing:{" "}
              {justUploaded.join(", ")}
            </span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
