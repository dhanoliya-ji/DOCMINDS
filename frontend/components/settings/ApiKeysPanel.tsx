/**
 * ApiKeysPanel.tsx
 * ================
 * Create, list and revoke API keys -- credentials for scripts and other
 * programs, sent as an `X-API-Key` header instead of signing in.
 *
 * THE KEY IS SHOWN EXACTLY ONCE
 * -----------------------------
 * The backend stores only a hash of each key (like a password), so after the
 * create response there is no way to see it again -- the list shows just a
 * short prefix to tell keys apart. The panel says so plainly next to the key,
 * because "I closed the tab and lost it" is otherwise a surprise.
 *
 * A KEY'S ROLE
 * ------------
 * A request made with a key acts with the LOWER of the key's role and its
 * owner's role. Give a reporting script a viewer key and a leak of it cannot
 * upload or delete anything.
 */

"use client";

import { KeyRound, Loader2 } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { Notice } from "@/components/AuthCard";
import { CopyButton } from "@/components/settings/MembersPanel";
import { ApiKey, ApiKeyCreated, RoleInput, api, getErrorMessage } from "@/lib/api";
import { ROLE_OPTIONS, roleLabel } from "@/lib/roles";

export default function ApiKeysPanel() {
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
      setError(getErrorMessage(err, "Could not load API keys."));
    }
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
      setError(getErrorMessage(err, "Could not create the key."));
    } finally {
      setCreating(false);
    }
  }

  async function revoke(key: ApiKey) {
    if (!window.confirm(`Revoke "${key.name}"? Anything using it will stop working.`)) return;
    setError("");
    try {
      await api.revokeApiKey(key.id);
      await load();
    } catch (err) {
      setError(getErrorMessage(err, "Could not revoke the key."));
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={create} className="glass flex flex-wrap items-end gap-3 p-5">
        <label className="min-w-[12rem] flex-1 text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            Key name
          </span>
          <input
            className="input"
            placeholder="e.g. Nightly report script"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={255}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            Role
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
            Expires in (days)
          </span>
          <input
            type="number"
            min={1}
            className="input"
            placeholder="Never"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <button type="submit" className="btn-primary" disabled={creating || !name.trim()}>
          {creating ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <KeyRound size={15} aria-hidden="true" />}
          Create key
        </button>
      </form>

      {created && (
        <Notice tone="success">
          Your new key for “{created.name}”. Copy it now — it will not be shown again:{" "}
          <code className="break-all" data-testid="new-api-key">
            {created.key}
          </code>{" "}
          <CopyButton text={created.key} label="Copy key" />
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      {keys === null ? (
        <div className="glass shimmer h-24" aria-busy="true" />
      ) : keys.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          No API keys yet.
        </p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead style={{ color: "var(--text-muted)" }}>
            <tr>
              <th className="py-2 font-medium">Name</th>
              <th className="py-2 font-medium">Key</th>
              <th className="py-2 font-medium">Role</th>
              <th className="py-2 font-medium">Last used</th>
              <th className="py-2 font-medium">
                <span className="sr-only">Actions</span>
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
                    {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "Never"}
                  </td>
                  <td className="py-2 text-right">
                    {revoked ? (
                      <span style={{ color: "var(--text-muted)" }}>Revoked</span>
                    ) : (
                      <button type="button" className="btn-ghost" onClick={() => revoke(k)} aria-label={`Revoke ${k.name}`}>
                        Revoke
                      </button>
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
