/**
 * MembersPanel.tsx
 * ================
 * The organization's people: who is in it, with what role, and who has been
 * invited. Admin only (every /org endpoint requires the admin role).
 *
 * WHAT AN ADMIN CAN DO HERE
 * -------------------------
 *   * invite someone by email with a role  -> POST   /org/invites
 *   * change a member's role               -> PATCH  /org/members/{id}
 *   * remove a member                      -> DELETE /org/members/{id}
 *   * revoke an invite not yet accepted    -> DELETE /org/invites/{id}
 *
 * WHAT IS DELIBERATELY NOT OFFERED
 * --------------------------------
 * Changing or removing YOURSELF. The backend refuses both (so an admin cannot
 * lock the organization out by demoting the last admin by accident), so the
 * controls are simply not drawn on your own row.
 *
 * THE INVITE LINK
 * ---------------
 * The create response carries the link once. It is emailed when SMTP is set
 * up, but shown here too, so an admin on a deployment without email can copy
 * it into a chat message instead.
 */

"use client";

import { Copy, Loader2, Trash2, UserPlus } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

import HoldToConfirm from "@/components/app/HoldToConfirm";
import { Notice } from "@/components/AuthCard";
import { Invite, InviteCreated, Member, RoleInput, api, getErrorMessage } from "@/lib/api";
import { ROLE_OPTIONS, roleAlias, roleLabel } from "@/lib/roles";

export default function MembersPanel({ currentUserId }: { currentUserId: string }) {
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
      setError(getErrorMessage(err, "Could not load members."));
    }
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
      setError(getErrorMessage(err, "Could not send the invite."));
    } finally {
      setInviting(false);
    }
  }

  /** Run a change, then reload; any refusal is shown in the backend's words. */
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
      {/* ---- Invite form ---- */}
      <form onSubmit={invite} className="glass flex flex-wrap items-end gap-3 p-5">
        <label className="min-w-[14rem] flex-1 text-sm">
          <span className="mb-1 block" style={{ color: "var(--text-secondary)" }}>
            Invite by email
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
            Role
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
          Send invite
        </button>
      </form>

      {created && (
        <Notice tone="success">
          Invite sent to {created.email}. You can also share this link (it works once, for that
          address only): <code className="break-all">{created.invite_url}</code>{" "}
          <CopyButton text={created.invite_url} label="Copy invite link" />
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      {/* ---- Members ---- */}
      <section aria-labelledby="members-heading">
        <h2 id="members-heading" className="mb-3 text-lg font-semibold">
          Members
        </h2>
        {members === null ? (
          <div className="glass shimmer h-24" aria-busy="true" />
        ) : (
          <table className="w-full text-left text-sm">
            <thead style={{ color: "var(--text-muted)" }}>
              <tr>
                <th className="py-2 font-medium">Email</th>
                <th className="py-2 font-medium">Role</th>
                <th className="py-2 font-medium">Status</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">Actions</span>
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
                      {isSelf && <span style={{ color: "var(--text-muted)" }}> (you)</span>}
                    </td>
                    <td className="py-2">
                      {isSelf ? (
                        roleLabel(m.role)
                      ) : (
                        <select
                          className="input py-1"
                          aria-label={`Role for ${m.email}`}
                          value={roleAlias(m.role)}
                          onChange={(e) =>
                            act(
                              () => api.updateMember(m.id, { role: e.target.value as RoleInput }),
                              "Could not change the role."
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
                      {!m.is_active ? "Deactivated" : m.is_verified ? "Active" : "Email not confirmed"}
                    </td>
                    <td className="py-2 text-right">
                      {!isSelf && (
                        // Hold to confirm: removing someone is not undoable here.
                        <HoldToConfirm
                          aria-label={`Remove ${m.email}`}
                          onConfirm={() => act(() => api.removeMember(m.id), "Could not remove the member.")}
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

      {/* ---- Pending invites ---- */}
      {pending.length > 0 && (
        <section aria-labelledby="invites-heading">
          <h2 id="invites-heading" className="mb-3 text-lg font-semibold">
            Pending invites
          </h2>
          <ul className="space-y-2">
            {pending.map((i) => (
              <li key={i.id} className="glass flex items-center justify-between px-4 py-2 text-sm">
                <span>
                  {i.email} · {roleLabel(i.role)} · expires {new Date(i.expires_at).toLocaleDateString()}
                </span>
                <HoldToConfirm
                  aria-label={`Revoke invite for ${i.email}`}
                  onConfirm={() => act(() => api.revokeInvite(i.id), "Could not revoke the invite.")}
                >
                  Hold to revoke
                </HoldToConfirm>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** Copy to the clipboard, confirming in place. Clipboard may be unavailable (http, old browsers). */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
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
      {copied ? "Copied" : label}
    </button>
  );
}
