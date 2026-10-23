/**
 * progress.ts
 * ===========
 * The line under a large document in the list: how far ingestion has got, or
 * that it is paused for the embedding rate limit and will resume by itself.
 * Kept out of lib/api.ts so screens that mock the API client still get it.
 */
import { type DocumentItem } from "@/lib/api";
import { translate, type Locale } from "@/lib/i18n";

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

/** A wait in whole units, rounded the way lib/api.ts `formatWait` does. */
function waitText(locale: Locale, seconds: number): string {
  if (seconds < 90)
    return translate(locale, seconds === 1 ? "progress.wait.second" : "progress.wait.seconds", { n: seconds });
  if (seconds < 90 * 60) return translate(locale, "progress.wait.minutes", { n: Math.ceil(seconds / 60) });
  if (seconds < 48 * 3600) return translate(locale, "progress.wait.hours", { n: Math.ceil(seconds / 3600) });
  return translate(locale, "progress.wait.days", { n: Math.ceil(seconds / 86400) });
}

/**
 * One line for the document list: "Embedding 340 of 1,200 chunks", etc.
 * The strings come from the i18n dictionary (lib/i18n/progress.ts); pass
 * the reader's `locale` from useT(). English is the default.
 */
export function describeProgress(
  progress: DocumentProgress | null,
  now: Date = new Date(),
  locale: Locale = "en",
): string | null {
  if (!progress) return null;
  const numberLocale = locale === "hi" ? "hi-IN" : "en-US";
  const n = (value: number | undefined | null) =>
    (value ?? 0).toLocaleString(numberLocale);
  const waitUntil = (iso: string | undefined) => {
    if (!iso) return "";
    const seconds = Math.max(
      0,
      Math.round((new Date(iso).getTime() - now.getTime()) / 1000),
    );
    return seconds > 0
      ? translate(locale, "progress.about", { duration: waitText(locale, seconds) })
      : "";
  };
  const t = (key: string, vars: Record<string, string>) => translate(locale, key, vars);
  switch (progress.stage) {
    case "extracting":
      return progress.pages_total
        ? t("progress.readingPages", { done: n(progress.pages_done), total: n(progress.pages_total) })
        : t("progress.readingFile", { chunks: n(progress.chunks) });
    case "embedding":
      return t("progress.embedding", { embedded: n(progress.embedded), total: n(progress.total) });
    case "waiting":
      return t("progress.waiting", {
        wait: waitUntil(progress.until),
        embedded: n(progress.embedded),
        total: n(progress.total),
      });
    case "paused":
      return t("progress.paused", {
        wait: waitUntil(progress.resume_at),
        embedded: n(progress.embedded),
        total: n(progress.total),
      });
    default:
      return null;
  }
}
