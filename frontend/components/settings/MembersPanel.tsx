// The organization's people: who is in it, with what role, and who has been invited.

"use client";

import { Copy, Loader2, Trash2, UserPlus } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

import HoldToConfirm from "@/components/app/HoldToConfirm";
import { Notice } from "@/components/AuthCard";
import { Invite, InviteCreated, Member, RoleInput, api, getErrorMessage } from "@/lib/api";
import { rich, useT } from "@/lib/i18n";
import { ROLE_OPTIONS, roleAlias, roleLabel } from "@/lib/roles";

export default function MembersPanel({ currentUserId }: { currentUserId: string }) {
  const { t } = useT();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [error, setError] = useState("");

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<RoleInput>("viewer");
  const [inviting, setInviting] = useState(false);
  const [created, setCreated] = useState<InviteCreated | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, i] = await Promise.all([api.listMembers(), api.listInvites()]);
      setMembers(m);
      setInvites(i);
    } catch (err) {
      setError(getErrorMessage(err, t("members.loadError")));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async: state is set after the fetch resolves, not in the effect body
    load();
  }, [load]);

  async function invite(event: FormEvent) {
    event.preventDefault();
    setError("");
    setCreated(null);
    setInviting(true);
    try {
      const result = await api.createInvite(email.trim(), role);
      setCreated(result);
      setEmail("");
      await load();
    } catch (err) {
      setError(getErrorMessage(err, t("members.inviteError")));
    } finally {
      setInviting(false);
    }
  }

  async function act(change: () => Promise<unknown>, fallback: string) {
    setError("");
    try {
      await change();
      await load();
    } catch (err) {
      setError(getErrorMessage(err, fallback));
    }
  }

  const pending = invites.filter((i) => i.status === "pending");

  return (
    <div className="space-y-6">
      <form onSubmit={invite} className="glass flex flex-wrap items-end gap-3 p-5">
        <label className="min-w-[14rem] flex-1 text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            {t("members.inviteLabel")}
          </span>
          <input
            type="email"
            className="input"
            placeholder="colleague@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            {t("settings.common.role")}
          </span>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value as RoleInput)}>
            {ROLE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label} — {o.help}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn-primary" disabled={inviting || !email.trim()}>
          {inviting ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <UserPlus size={15} aria-hidden="true" />}
          {t("members.send")}
        </button>
      </form>

      {created && (
        <Notice tone="success">
          {rich(t("members.sent"), {
            email: created.email,
            link: <code className="break-all">{created.invite_url}</code>,
            copy: <CopyButton text={created.invite_url} label={t("members.copyLink")} />,
          })}
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      <section aria-labelledby="members-heading">
        <h2 id="members-heading" className="mb-3 text-lg font-semibold">
          {t("members.heading")}
        </h2>
        {members === null ? (
          <div className="glass shimmer h-24" aria-busy="true" />
        ) : (
          <table className="w-full text-left text-sm">
            <thead style={{ color: "var(--text-muted)" }}>
              <tr>
                <th className="py-2 font-medium">{t("members.email")}</th>
                <th className="py-2 font-medium">{t("settings.common.role")}</th>
                <th className="py-2 font-medium">{t("members.status")}</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">{t("settings.common.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => {
                const isSelf = m.id === currentUserId;
                return (
                  <tr key={m.id} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                    <td className="py-2">
                      {m.email}
                      {isSelf && <span style={{ color: "var(--text-muted)" }}>{t("members.you")}</span>}
                    </td>
                    <td className="py-2">
                      {isSelf ? (
                        roleLabel(m.role)
                      ) : (
                        <select
                          className="input py-1"
                          aria-label={t("members.roleFor", { email: m.email })}
                          value={roleAlias(m.role)}
                          onChange={(e) =>
                            act(
                              () => api.updateMember(m.id, { role: e.target.value as RoleInput }),
                              t("members.roleError")
                            )
                          }
                        >
                          {ROLE_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                    <td className="py-2" style={{ color: "var(--text-secondary)" }}>
                      {!m.is_active
                        ? t("members.deactivated")
                        : m.is_verified
                          ? t("members.active")
                          : t("members.unconfirmed")}
                    </td>
                    <td className="py-2 text-right">
                      {!isSelf && (
                        <HoldToConfirm
                          aria-label={t("members.remove", { email: m.email })}
                          onConfirm={() => act(() => api.removeMember(m.id), t("members.removeError"))}
                        >
                          <Trash2 size={15} aria-hidden="true" />
                        </HoldToConfirm>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {pending.length > 0 && (
        <section aria-labelledby="invites-heading">
          <h2 id="invites-heading" className="mb-3 text-lg font-semibold">
            {t("members.pending")}
          </h2>
          <ul className="space-y-2">
            {pending.map((i) => (
              <li key={i.id} className="glass flex items-center justify-between px-4 py-2 text-sm">
                <span>
                  {t("members.expires", {
                    email: i.email,
                    role: roleLabel(i.role),
                    date: new Date(i.expires_at).toLocaleDateString(),
                  })}
                </span>
                <HoldToConfirm
                  aria-label={t("members.revokeInvite", { email: i.email })}
                  onConfirm={() => act(() => api.revokeInvite(i.id), t("members.revokeError"))}
                >
                  {t("settings.common.holdRevoke")}
                </HoldToConfirm>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const { t } = useT();
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 underline"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        } catch {
          /* the text is on screen to copy by hand */
        }
      }}
    >
      <Copy size={13} aria-hidden="true" />
      {copied ? t("settings.common.copied") : label}
    </button>
  );
}
