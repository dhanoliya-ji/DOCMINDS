/**
 * VerifyBanner.tsx
 * ================
 * A reminder shown to a signed-in user whose email is not yet confirmed, with
 * a button to send the confirmation link again.
 *
 * WHY IT EXISTS
 * -------------
 * Verification emails get lost, land in spam, or expire (after
 * EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS). Without a way to ask for another, a
 * user's only recourse would be an admin -- or, when
 * REQUIRE_EMAIL_VERIFICATION is on, a locked account. The resend endpoint is
 * rate limited server-side, so the button needs no throttle of its own.
 */

"use client";

import { Loader2, MailWarning } from "lucide-react";
import { useState } from "react";

import { api, getErrorMessage } from "@/lib/api";

export default function VerifyBanner({ email }: { email: string }) {
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function resend() {
    setSending(true);
    setMessage("");
    try {
      const reply = await api.resendVerification();
      setFailed(false);
      setMessage(reply.detail || `A new link is on its way to ${email}.`);
    } catch (err) {
      setFailed(true);
      setMessage(getErrorMessage(err, "Could not send the link. Please try again."));
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      className="glass mb-6 flex flex-wrap items-start gap-3 p-4"
      style={{ borderColor: "rgba(251,191,36,0.3)" }}
      role="region"
      aria-label="Email confirmation"
    >
      <MailWarning size={17} className="mt-0.5 shrink-0" style={{ color: "var(--warning)" }} aria-hidden="true" />
      <div className="min-w-0 flex-1 text-sm">
        <div className="font-medium" style={{ color: "var(--warning)" }}>
          Please confirm your email address
        </div>
        <p className="mt-1" style={{ color: "var(--text-secondary)" }}>
          We sent a link to <strong>{email}</strong>. Click it to confirm this address.
        </p>
        {message && (
          <p
            role={failed ? "alert" : "status"}
            className="mt-2"
            style={{ color: failed ? "var(--danger)" : "var(--success)" }}
          >
            {message}
          </p>
        )}
      </div>
      <button type="button" onClick={resend} disabled={sending} className="btn-ghost text-sm">
        {sending && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
        Resend link
      </button>
    </div>
  );
}
