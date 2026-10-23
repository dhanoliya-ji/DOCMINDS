/**
 * ShareDialog.tsx
 * ===============
 * Make a public read-only link to an answer, a conversation or a project,
 * and manage the links that already exist.
 *
 *     1. pick when it expires (never, 7, 30 or 90 days) -> "Create link"
 *     2. the full link appears ONCE, with a Copy button. The server keeps
 *        only an HMAC of the token, so it cannot be shown again: a lost link
 *        is revoked and replaced.
 *     3. underneath, every link to this target: its prefix, status, views,
 *        and Revoke.
 *
 * The visitor's page is app/share/[token]/page.tsx.
 */

"use client";

import { Check, Copy, Link2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, getErrorMessage, type ShareKind, type ShareLink } from "@/lib/api";
import { copyText } from "@/lib/export";
import { useT } from "@/lib/i18n";

const EXPIRY_CHOICES = [0, 7, 30, 90];

interface Props {
  kind: ShareKind;
  targetId: string;
  onClose: () => void;
}

export function shareUrl(path: string): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return `${origin}${path}`;
}

export default function ShareDialog({ kind, targetId, onClose }: Props) {
  const { t } = useT();
  const [expiry, setExpiry] = useState(0);
  const [links, setLinks] = useState<ShareLink[]>([]);
  const [created, setCreated] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listShareLinks(kind, targetId)
      .then((list) => !cancelled && setLinks(list))
      .catch((err) => !cancelled && setError(getErrorMessage(err, "Could not load the links.")));
    dialogRef.current?.focus();
    return () => {
      cancelled = true;
    };
  }, [kind, targetId]);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const link = await api.createShareLink(kind, targetId, expiry || null);
      setCreated(shareUrl(link.path));
      setCopied(false);
      // The list never holds the token itself.
      const listed: ShareLink = { ...link };
      delete (listed as Partial<typeof link>).token;
      delete (listed as Partial<typeof link>).path;
      setLinks((current) => [listed, ...current]);
    } catch (err) {
      setError(getErrorMessage(err, "Could not create the link."));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (created && (await copyText(created))) setCopied(true);
  }

  async function revoke(id: string) {
    try {
      await api.revokeShareLink(id);
      setLinks((current) =>
        current.map((l) => (l.id === id ? { ...l, status: "revoked", revoked_at: new Date().toISOString() } : l))
      );
    } catch (err) {
      setError(getErrorMessage(err, "Could not revoke the link."));
    }
  }

  const heading = t(kind === "message" ? "share.answer" : kind === "session" ? "share.conversation" : "share.project");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--overlay)" }}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-dialog-title"
        tabIndex={-1}
        className="glass w-full max-w-md p-5 outline-none"
        style={{ background: "var(--bg-elevated)" }}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
      >
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 id="share-dialog-title" className="flex items-center gap-2 text-base font-semibold">
            <Link2 size={16} aria-hidden="true" /> {heading}
          </h2>
          <button type="button" onClick={onClose} aria-label={t("share.close")} className="p-1">
            <X size={16} />
          </button>
        </div>
        <p className="mb-4 text-xs" style={{ color: "var(--text-secondary)" }}>
          {t("share.intro")}
        </p>

        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-1 flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
            {t("share.expiry")}
            <select className="input py-2 text-sm" value={expiry} onChange={(e) => setExpiry(Number(e.target.value))}>
              {EXPIRY_CHOICES.map((days) => (
                <option key={days} value={days}>
                  {days ? t("share.expiry.days", { days }) : t("share.expiry.never")}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="btn-primary text-sm" onClick={create} disabled={busy}>
            {busy ? t("share.creating") : t("share.create")}
          </button>
        </div>

        {created && (
          <div className="mt-4 rounded-xl p-3" style={{ background: "var(--surface-1)" }}>
            <div className="flex items-center gap-2">
              <input className="input flex-1 py-2 text-xs" readOnly value={created} aria-label={t("share.copy")} onFocus={(e) => e.target.select()} />
              <button type="button" className="btn-ghost text-xs" onClick={copy}>
                {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
                {copied ? t("share.copied") : t("share.copy")}
              </button>
            </div>
            <p className="mt-2 text-xs" style={{ color: "var(--warning)" }}>
              {t("share.onceOnly")}
            </p>
          </div>
        )}

        {error && (
          <p role="alert" className="mt-3 text-xs" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}

        <h3 className="mb-2 mt-5 text-xs font-semibold" style={{ color: "var(--text-secondary)" }}>
          {t("share.existing")}
        </h3>
        {links.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            {t("share.none")}
          </p>
        ) : (
          <ul className="max-h-48 space-y-1.5 overflow-y-auto text-xs">
            {links.map((link) => (
              <li key={link.id} className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate font-mono" style={{ color: "var(--text-secondary)" }}>
                  {link.token_prefix}…
                </span>
                <span style={{ color: "var(--text-muted)" }}>
                  {t(`share.status.${link.status}`)} · {t("share.views", { count: link.view_count ?? 0 })}
                  {link.expires_at ? ` · ${new Date(link.expires_at).toLocaleDateString()}` : ""}
                </span>
                {link.status === "active" && (
                  <button type="button" className="font-medium" style={{ color: "var(--danger)" }} onClick={() => revoke(link.id)}>
                    {t("share.revoke")}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
