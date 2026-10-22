/**
 * AuditLogPanel.tsx
 * =================
 * A paginated table of who did what in the organization (GET /org/audit-logs):
 * sign-ins, failed sign-ins, invites, role changes, API keys, uploads.
 *
 * PAGINATION
 * ----------
 * The log only grows, so it is fetched one page at a time (`limit` + `offset`)
 * and the backend returns the `total` so the panel can say "26–50 of 312" and
 * know when "Next" has nowhere to go.
 *
 * THE FILTER
 * ----------
 * `action` is either an exact action ("user.login") or a prefix ending in "."
 * ("member.") -- the backend's rule, passed through unchanged.
 */

"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import { Notice } from "@/components/AuthCard";
import { AuditLogPage, api, getErrorMessage } from "@/lib/api";

export const AUDIT_PAGE_SIZE = 25;

export default function AuditLogPanel() {
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
      .catch((err) => !cancelled && setError(getErrorMessage(err, "Could not load the audit log.")));
    return () => {
      cancelled = true;
    };
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
          aria-label="Filter by action"
          placeholder='Action, e.g. "user.login" or "member."'
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" className="btn-ghost">
          Filter
        </button>
      </form>

      {error && <Notice tone="error">{error}</Notice>}

      {page === null ? (
        <div className="glass shimmer h-40" aria-busy="true" />
      ) : page.items.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          No matching events.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Audit log</caption>
            <thead style={{ color: "var(--text-muted)" }}>
              <tr>
                <th className="py-2 pr-3 font-medium">When</th>
                <th className="py-2 pr-3 font-medium">Action</th>
                <th className="py-2 pr-3 font-medium">User</th>
                <th className="py-2 pr-3 font-medium">IP</th>
                <th className="py-2 font-medium">Details</th>
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
        <nav aria-label="Audit log pages" className="flex items-center justify-between text-sm">
          <span style={{ color: "var(--text-secondary)" }}>
            {first}–{last} of {page.total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-ghost"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE))}
            >
              <ChevronLeft size={15} aria-hidden="true" /> Previous
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={offset + AUDIT_PAGE_SIZE >= page.total}
              onClick={() => setOffset(offset + AUDIT_PAGE_SIZE)}
            >
              Next <ChevronRight size={15} aria-hidden="true" />
            </button>
          </div>
        </nav>
      )}
    </div>
  );
}
