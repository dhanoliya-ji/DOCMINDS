// Create, list and revoke API keys -- credentials for scripts and other programs, sent...

"use client";

import { KeyRound, Loader2 } from "lucide-react";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import HoldToConfirm from "@/components/app/HoldToConfirm";
import { Notice } from "@/components/AuthCard";
import { CopyButton } from "@/components/settings/MembersPanel";
import { ApiKey, ApiKeyCreated, RoleInput, api, getErrorMessage } from "@/lib/api";
import { rich, useT } from "@/lib/i18n";
import { ROLE_OPTIONS, roleLabel } from "@/lib/roles";

export default function ApiKeysPanel() {
  const { t } = useT();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<RoleInput>("viewer");
  const [days, setDays] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const load = useCallback(async () => {
    try {
      setKeys(await api.listApiKeys());
    } catch (err) {
      setError(getErrorMessage(err, t("apiKeys.loadError")));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async: state is set after the fetch resolves
    load();
  }, [load]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setCreating(true);
    try {
      const expires = days.trim() ? Number(days) : undefined;
      setCreated(await api.createApiKey(name.trim(), role, expires));
      setName("");
      setDays("");
      await load();
    } catch (err) {
      setError(getErrorMessage(err, t("apiKeys.createError")));
    } finally {
      setCreating(false);
    }
  }

  async function revoke(key: ApiKey) {
    setError("");
    try {
      await api.revokeApiKey(key.id);
      await load();
    } catch (err) {
      setError(getErrorMessage(err, t("apiKeys.revokeError")));
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={create} className="glass flex flex-wrap items-end gap-3 p-5">
        <label className="min-w-[12rem] flex-1 text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            {t("apiKeys.name")}
          </span>
          <input
            className="input"
            placeholder={t("apiKeys.namePlaceholder")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={255}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            {t("settings.common.role")}
          </span>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value as RoleInput)}>
            {ROLE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="w-36 text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            {t("apiKeys.expires")}
          </span>
          <input
            type="number"
            min={1}
            className="input"
            placeholder={t("settings.common.never")}
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <button type="submit" className="btn-primary" disabled={creating || !name.trim()}>
          {creating ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <KeyRound size={15} aria-hidden="true" />}
          {t("apiKeys.create")}
        </button>
        <p className="w-full text-xs" style={{ color: "var(--text-muted)" }}>
          {rich(t("apiKeys.docs"), {
            link: (
              <Link href="/developers" className="underline" style={{ color: "var(--accent-hover)" }}>
                {t("apiKeys.docsLink")}
              </Link>
            ),
          })}
        </p>
      </form>

      {created && (
        <Notice tone="success">
          {rich(t("apiKeys.created"), {
            name: created.name,
            key: (
              <code className="break-all" data-testid="new-api-key">
                {created.key}
              </code>
            ),
            copy: <CopyButton text={created.key} label={t("apiKeys.copy")} />,
          })}
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      {keys === null ? (
        <div className="glass shimmer h-24" aria-busy="true" />
      ) : keys.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          {t("apiKeys.empty")}
        </p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead style={{ color: "var(--text-muted)" }}>
            <tr>
              <th className="py-2 font-medium">{t("apiKeys.colName")}</th>
              <th className="py-2 font-medium">{t("apiKeys.colKey")}</th>
              <th className="py-2 font-medium">{t("settings.common.role")}</th>
              <th className="py-2 font-medium">{t("apiKeys.colLastUsed")}</th>
              <th className="py-2 font-medium">
                <span className="sr-only">{t("settings.common.actions")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const revoked = Boolean(k.revoked_at) || !k.is_active;
              return (
                <tr key={k.id} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                  <td className="py-2">{k.name}</td>
                  <td className="py-2">
                    <code>{k.key_prefix}…</code>
                  </td>
                  <td className="py-2">{roleLabel(k.role)}</td>
                  <td className="py-2" style={{ color: "var(--text-secondary)" }}>
                    {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : t("settings.common.never")}
                  </td>
                  <td className="py-2 text-right">
                    {revoked ? (
                      <span style={{ color: "var(--text-muted)" }}>{t("apiKeys.revoked")}</span>
                    ) : (
                      <HoldToConfirm onConfirm={() => revoke(k)} aria-label={t("apiKeys.revoke", { name: k.name })}>
                        {t("settings.common.holdRevoke")}
                      </HoldToConfirm>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
