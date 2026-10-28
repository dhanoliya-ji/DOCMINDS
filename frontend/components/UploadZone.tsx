// The drag-and-drop upload area -- for one file or fifty.

"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, Check, CheckCircle2, Loader2, UploadCloud, X, XCircle } from "lucide-react";
import { DragEvent, useRef, useState } from "react";

import { usePrefersReducedMotion } from "@/components/landing/hooks";
import { ApiError, api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

export function uploadErrorMessage(error: unknown): string {
  const detail = getErrorMessage(error, "Upload failed.");
  if (!(error instanceof ApiError)) return detail;
  switch (error.status) {
    case 413:
      return `File too large: ${detail}`;
    case 415:
      return `Unsupported file: ${detail}`;
    case 429:
      return `Too many uploads: ${detail}`;
    case 402:
      return `Quota reached: ${detail}`;
    case 503:
      return `Upload unavailable: ${detail}`;
    default:
      return detail;
  }
}

const ACCEPTED =
  ".pdf,.docx,.pptx,.xlsx,.xls,.html,.htm,.eml,.txt,.md,.markdown,.csv,.zip,.png,.jpg,.jpeg,.tiff,.bmp,.gif";

const CONCURRENCY = 3;

export interface UploadRow {
  key: string;
  name: string;
  size: number;
  status: "queued" | "uploading" | "done" | "error";
  progress: number;
  message?: string;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

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

export type ZonePhase = "idle" | "drag-over" | "uploading" | "processing" | "done" | "error";

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

const PHASE_TEXT: Record<ZonePhase, string> = {
  idle: "upload.idle",
  "drag-over": "upload.dragOver",
  uploading: "upload.uploading",
  processing: "upload.processing",
  done: "upload.done",
  error: "upload.error",
};

const PHASE_COLOR: Record<ZonePhase, string> = {
  idle: "var(--accent-hover)",
  "drag-over": "var(--accent)",
  uploading: "var(--accent)",
  processing: "var(--violet)",
  done: "var(--success)",
  error: "var(--danger)",
};

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
  onUploaded: () => void;
}

export default function UploadZone({ projectId, onUploaded }: Props) {
  const [dragging, setDragging] = useState(false);
  const [rows, setRows] = useState<UploadRow[]>([]);
  const nextKey = useRef(0);
  const { t } = useT();
  const reduced = usePrefersReducedMotion();

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
    setRows((current) => [...current.filter((r) => r.status === "error"), ...batch.map((b) => b.row)]);

    await runPool(batch, CONCURRENCY, async ({ file, row }) => {
      update(row.key, { status: "uploading" });
      try {
        await api.uploadFile(projectId, file, (fraction) => update(row.key, { progress: fraction }));
        update(row.key, { status: "done", progress: 1 });
        onUploaded();
      } catch (err) {
        update(row.key, { status: "error", message: uploadErrorMessage(err) });
      }
    });

    const keys = new Set(batch.map((b) => b.row.key));
    setTimeout(
      () => setRows((current) => current.filter((r) => !(keys.has(r.key) && r.status === "done"))),
      4000
    );
  }


  function handleDragOver(event: DragEvent<HTMLDivElement>) {
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

    upload(Array.from(event.dataTransfer.files));
  }

  const done = rows.filter((r) => r.status === "done").length;
  const failed = rows.filter((r) => r.status === "error").length;
  const phase = zonePhase(dragging, rows);
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
        onKeyDown={(e) => {
          if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        animate={{ scale: phase === "drag-over" && !reduced ? 1.01 : 1 }}
        transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 300, damping: 24 }}
        className="glass glass-hover flex cursor-pointer flex-col items-center justify-center px-6 py-10 text-center transition-colors"
        style={{
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

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED}
          className="hidden"
          data-testid="file-input"
          onChange={(e) => {
            upload(Array.from(e.target.files || []));
            e.target.value = "";
          }}
        />
      </motion.div>

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
