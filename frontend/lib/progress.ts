/**
 * progress.ts
 * ===========
 * The line under a large document in the list: how far ingestion has got, or
 * that it is paused for the embedding rate limit and will resume by itself.
 * Kept out of lib/api.ts so screens that mock the API client still get it.
 */
import { formatWait, type DocumentItem } from "@/lib/api";

/**
 * Where a large document's ingestion has got to: meta_data.progress, written
 * by the backend after every batch (tasks/ingestion.py).
 *   extracting  reading pages      embedding  making vectors
 *   waiting     held by the embedding rate limit, mid-batch
 *   paused      the rate limit outlasted the retries; resumes at resume_at
 */
export interface DocumentProgress {
  stage:
    | "extracting"
    | "embedding"
    | "waiting"
    | "paused"
    | "retrying"
    | "completed"
    | "failed";
  pages_done?: number;
  pages_total?: number | null;
  chunks?: number;
  embedded?: number;
  total?: number;
  resume_at?: string;
  until?: string;
  reason?: string;
}

/** The document's progress object, or null when the backend sent none. */
export function documentProgress(
  doc: Pick<DocumentItem, "meta_data">,
): DocumentProgress | null {
  const raw = (doc.meta_data as unknown as { progress?: unknown } | null)
    ?.progress;
  return raw && typeof raw === "object" && "stage" in raw
    ? (raw as DocumentProgress)
    : null;
}

/** One line for the document list: "Embedding 340 of 1,200 chunks", etc. */
export function describeProgress(
  progress: DocumentProgress | null,
  now: Date = new Date(),
): string | null {
  if (!progress) return null;
  const n = (value: number | undefined | null) =>
    (value ?? 0).toLocaleString("en-US");
  const waitUntil = (iso: string | undefined) => {
    if (!iso) return "";
    const seconds = Math.max(
      0,
      Math.round((new Date(iso).getTime() - now.getTime()) / 1000),
    );
    return seconds > 0 ? ` (about ${formatWait(seconds)})` : "";
  };
  switch (progress.stage) {
    case "extracting":
      return progress.pages_total
        ? `Reading page ${n(progress.pages_done)} of ${n(progress.pages_total)}`
        : `Reading the file (${n(progress.chunks)} chunks so far)`;
    case "embedding":
      return `Embedding ${n(progress.embedded)} of ${n(progress.total)} chunks`;
    case "waiting":
      return `Waiting for the embedding rate limit${waitUntil(progress.until)}: ${n(progress.embedded)} of ${n(progress.total)} chunks done`;
    case "paused":
      return `Paused for the embedding rate limit${waitUntil(progress.resume_at)}: ${n(progress.embedded)} of ${n(progress.total)} chunks done, resumes automatically`;
    default:
      return null;
  }
}
