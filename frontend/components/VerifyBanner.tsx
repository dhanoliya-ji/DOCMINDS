// A reminder shown to a signed-in user whose email is not yet confirmed, with a button...

"use client";

import { Loader2, MailWarning } from "lucide-react";
import { useState } from "react";

import { api, getErrorMessage } from "@/lib/api";
import { rich, useT } from "@/lib/i18n";

export default function VerifyBanner({ email }: { email: string }) {
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const { t } = useT();

  async function resend() {
    setSending(true);
    setMessage("");
    try {
      const reply = await api.resendVerification();
      setFailed(false);
      setMessage(reply.detail || t("verify.sent", { email }));
    } catch (err) {
      setFailed(true);
      setMessage(getErrorMessage(err, t("verify.error")));
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      className="glass mb-6 flex flex-wrap items-start gap-3 p-4"
      style={{ borderColor: "rgba(251,191,36,0.3)" }}
      role="region"
      aria-label={t("verify.region")}
    >
      <MailWarning size={17} className="mt-0.5 shrink-0" style={{ color: "var(--warning)" }} aria-hidden="true" />
      <div className="min-w-0 flex-1 text-sm">
        <div className="font-medium" style={{ color: "var(--warning)" }}>
          {t("verify.title")}
        </div>
        <p className="mt-1" style={{ color: "var(--text-secondary)" }}>
          {rich(t("verify.body"), { email: <strong>{email}</strong> })}
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
        {t("verify.resend")}
      </button>
    </div>
  );
}
