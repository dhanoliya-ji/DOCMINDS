/**
 * share/[token]/page.tsx  (served at "/share/<token>")
 * ====================================================
 * The PUBLIC, read-only view of a share link -- no account needed.
 *
 *     an answer         the question and the answer, with its sources and
 *                       confidence badge, rendered exactly as in the app
 *                       (tables and charts included)
 *     a conversation    every message, in order
 *     a project         its name and the documents in it
 *
 * The backend decides what a visitor may see (an allow-list in
 * services/sharing.py: no ids, no emails, no organization). This page only
 * renders it, and fetches WITHOUT the signed-in user's token, so someone
 * signed in sees exactly what any visitor sees.
 *
 * A dead link -- unknown, expired, revoked, or its target deleted -- gets
 * one message: the server does not say which, and neither does this page.
 */

"use client";

import { FileText, Lock, MessageSquare } from "lucide-react";
import Link from "next/link";
import { use, useEffect, useState } from "react";

import AnswerMarkdown from "@/components/AnswerMarkdown";
import ConfidenceBadge from "@/components/ConfidenceBadge";
import ThemeToggle from "@/components/app/ThemeToggle";
import { api, type Confidence, type PublicShare } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Message = PublicShare["messages"] extends (infer M)[] | undefined ? M : never;

export default function SharedPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const { t } = useT();
  const [share, setShare] = useState<PublicShare | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getPublicShare(token)
      .then((view) => !cancelled && setShare(view))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [token]);

  const heading = share
    ? t(share.kind === "message" ? "public.sharedAnswer" : share.kind === "session" ? "public.sharedConversation" : "public.sharedProject")
    : "";

  return (
    <div className="min-h-screen px-4 py-8">
      <div className="mx-auto max-w-3xl">
        <header className="mb-8 flex items-center justify-between gap-3">
          <Link href="/" className="text-gradient text-lg font-bold">
            DocMinds
          </Link>
          <ThemeToggle />
        </header>

        {failed ? (
          <div className="glass px-6 py-16 text-center">
            <Lock size={22} className="mx-auto mb-3" aria-hidden="true" style={{ color: "var(--text-muted)" }} />
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              {t("public.notFound")}
            </p>
          </div>
        ) : !share ? (
          <p className="text-sm" role="status" style={{ color: "var(--text-muted)" }}>
            {t("public.loading")}
          </p>
        ) : (
          <main>
            <p className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--accent-hover)" }}>
              {heading}
            </p>
            <h1 className="mt-1 break-words text-2xl font-bold">{share.title}</h1>
            <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
              {t("public.readOnly")}
              {share.expires_at ? ` ${t("public.expires", { date: new Date(share.expires_at).toLocaleDateString() })}` : ""}
            </p>

            {share.kind === "project" ? (
              <section className="mt-6">
                <h2 className="mb-3 text-sm font-semibold">{t("public.documents")}</h2>
                {(share.documents ?? []).length === 0 ? (
                  <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                    {t("public.noDocuments")}
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {(share.documents ?? []).map((d, i) => (
                      <li key={`${i}-${d.filename}`} className="glass flex items-center gap-3 p-3 text-sm">
                        <FileText size={15} aria-hidden="true" style={{ color: "var(--accent-hover)" }} />
                        <span className="min-w-0 flex-1 truncate">{d.filename}</span>
                        <span className="text-xs uppercase" style={{ color: "var(--text-muted)" }}>
                          {d.file_type}
                          {d.page_count ? ` · ${d.page_count} pages` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : (
              <ol className="mt-6 space-y-5">
                {(share.messages ?? []).map((message, i) => (
                  <SharedMessage key={i} message={message} />
                ))}
              </ol>
            )}
          </main>
        )}
      </div>
    </div>
  );
}

function SharedMessage({ message }: { message: Message }) {
  const { t } = useT();
  const isUser = message.role === "user";
  const citations = message.citations ?? [];
  return (
    <li>
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"
          style={{ background: isUser ? "var(--surface-2)" : "rgba(99,102,241,0.16)", color: "var(--accent-hover)" }}
          aria-hidden="true"
        >
          <MessageSquare size={13} />
        </span>
        <div className="min-w-0 flex-1">
          <div
            className="rounded-2xl px-4 py-3 text-sm leading-relaxed"
            style={{ background: isUser ? "var(--surface-2)" : "var(--surface-1)" }}
          >
            {isUser ? <div className="whitespace-pre-wrap">{message.content}</div> : <AnswerMarkdown>{message.content}</AnswerMarkdown>}
          </div>
          {!isUser && (
            <div className="mt-2 space-y-2">
              <ConfidenceBadge confidence={message.confidence as Confidence | null | undefined} />
              {citations.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer" style={{ color: "var(--accent-hover)" }}>
                    {t("public.sources")} ({citations.length})
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {citations.map((c, i) => (
                      <li key={i} className="glass p-3">
                        <span className="font-medium" style={{ color: "var(--accent-hover)" }}>
                          [{c.number}] {c.filename}
                          {c.page_number ? ` · ${t("public.page", { page: c.page_number })}` : ""}
                        </span>
                        <p className="mt-1" style={{ color: "var(--text-secondary)" }}>
                          {c.snippet}
                        </p>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
