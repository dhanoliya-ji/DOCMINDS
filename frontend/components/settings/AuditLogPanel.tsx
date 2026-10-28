// A paginated table of who did what in the organization (GET /org/audit-logs): sign-ins...

"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import { Notice } from "@/components/AuthCard";
import { AuditLogPage, api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

export const AUDIT_PAGE_SIZE = 25;

export default function AuditLogPanel() {
  const { t } = useT();
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState<AuditLogPage | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .listAuditLogs({ limit: AUDIT_PAGE_SIZE, offset, action: action || undefined })
      .then((p) => {
        if (!cancelled) {
          setPage(p);
          setError("");
        }
      })
      .catch((err) => !cancelled && setError(getErrorMessage(err, t("audit.loadError"))));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, action]);

  function applyFilter(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setAction(draft.trim());
  }

  const first = page && page.total > 0 ? offset + 1 : 0;
  const last = page ? Math.min(offset + page.items.length, page.total) : 0;

  return (
    <div className="space-y-4">
      <form onSubmit={applyFilter} className="flex flex-wrap gap-2">
        <input
          className="input max-w-xs"
          aria-label={t("audit.filterLabel")}
          placeholder={t("audit.filterPlaceholder")}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" className="btn-ghost">
          {t("audit.filter")}
        </button>
      </form>

      {error && <Notice tone="error">{error}</Notice>}

      {page === null ? (
        <div className="glass shimmer h-40" aria-busy="true" />
      ) : page.items.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          {t("audit.empty")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">{t("audit.caption")}</caption>
            <thead style={{ color: "var(--text-muted)" }}>
              <tr>
                <th className="py-2 pr-3 font-medium">{t("audit.when")}</th>
                <th className="py-2 pr-3 font-medium">{t("audit.action")}</th>
                <th className="py-2 pr-3 font-medium">{t("audit.user")}</th>
                <th className="py-2 pr-3 font-medium">{t("audit.ip")}</th>
                <th className="py-2 font-medium">{t("audit.details")}</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.id} className="border-t align-top" style={{ borderColor: "var(--border-subtle)" }}>
                  <td className="whitespace-nowrap py-2 pr-3">{new Date(item.created_at).toLocaleString()}</td>
                  <td className="py-2 pr-3">
                    <code>{item.action}</code>
                  </td>
                  <td className="py-2 pr-3" style={{ color: "var(--text-secondary)" }}>
                    {item.user_id ? item.user_id.slice(0, 8) : "—"}
                  </td>
                  <td className="py-2 pr-3" style={{ color: "var(--text-secondary)" }}>
                    {item.ip_address || "—"}
                  </td>
                  <td className="py-2 text-xs" style={{ color: "var(--text-secondary)" }}>
                    {Object.keys(item.details || {}).length ? JSON.stringify(item.details) : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {page && page.total > 0 && (
        <nav aria-label={t("audit.pages")} className="flex items-center justify-between text-sm">
          <span style={{ color: "var(--text-secondary)" }}>
            {t("audit.range", { first, last, total: page.total })}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-ghost"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE))}
            >
              <ChevronLeft size={15} aria-hidden="true" /> {t("audit.previous")}
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={offset + AUDIT_PAGE_SIZE >= page.total}
              onClick={() => setOffset(offset + AUDIT_PAGE_SIZE)}
            >
              {t("audit.next")} <ChevronRight size={15} aria-hidden="true" />
            </button>
          </div>
        </nav>
      )}
    </div>
  );
}
