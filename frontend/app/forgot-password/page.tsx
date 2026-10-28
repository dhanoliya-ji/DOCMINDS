// Page /forgot-password -- ask for a password-reset link by email.

"use client";

import { ArrowLeft, Loader2, Mail } from "lucide-react";
import Link from "next/link";
import { FormEvent, useState } from "react";

import { AuthCard, Notice } from "@/components/AuthCard";
import { api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

export default function ForgotPasswordPage() {
  const { t } = useT();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState("");
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const reply = await api.requestPasswordReset(email);
      setSent(
        reply.detail ||
          "If an account exists for that address, we've emailed it a link to reset the password."
      );
    } catch (err) {
      setError(getErrorMessage(err, "Could not send the reset link. Please try again."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard title={t("auth.resetTitle")} subtitle={t("auth.resetSubtitle")}>
      {sent ? (
        <Notice tone="success">{sent}</Notice>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="relative">
            <Mail
              size={16}
              aria-hidden="true"
              className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
              style={{ color: "var(--text-muted)" }}
            />
            <input
              type="email"
              aria-label={t("auth.email")}
              autoComplete="email"
              className="input pl-11"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          {error && <Notice tone="error">{error}</Notice>}
          <button type="submit" disabled={loading} className="btn-primary w-full py-3">
            {loading && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
            {loading ? t("auth.sending") : t("auth.sendReset")}
          </button>
        </form>
      )}
      <div className="mt-6 text-center text-sm">
        <Link
          href="/login"
          className="inline-flex items-center gap-1"
          style={{ color: "var(--accent-hover)" }}
        >
          <ArrowLeft size={14} aria-hidden="true" /> {t("auth.backToSignIn")}
        </Link>
      </div>
    </AuthCard>
  );
}
