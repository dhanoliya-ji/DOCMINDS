/**
 * forgot-password/page.tsx  (served at "/forgot-password")
 * ========================================================
 * Ask for a password-reset link by email.
 *
 * THE SAME ANSWER FOR EVERY ADDRESS
 * ---------------------------------
 * POST /auth/password-reset/request replies identically whether or not the
 * address has an account. If it said "no such user", anyone could find out
 * who has an account by typing addresses in. So this page also shows one
 * message for every outcome -- "if an account exists, a link is on its way" --
 * and never hints which case happened.
 *
 * The emailed link points at /reset-password?token=... (see
 * backend/app/services/email.py).
 */

"use client";

import { ArrowLeft, Loader2, Mail } from "lucide-react";
import Link from "next/link";
import { FormEvent, useState } from "react";

import { AuthCard, Notice } from "@/components/AuthCard";
import { api, getErrorMessage } from "@/lib/api";

export default function ForgotPasswordPage() {
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
      // Realistically only a 429 (too many requests) or a network failure.
      setError(getErrorMessage(err, "Could not send the reset link. Please try again."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard title="Reset your password" subtitle="We'll email you a link to choose a new one.">
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
              aria-label="Email"
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
            {loading ? "Sending..." : "Send reset link"}
          </button>
        </form>
      )}
      <div className="mt-6 text-center text-sm">
        <Link
          href="/login"
          className="inline-flex items-center gap-1"
          style={{ color: "var(--accent-hover)" }}
        >
          <ArrowLeft size={14} aria-hidden="true" /> Back to sign in
        </Link>
      </div>
    </AuthCard>
  );
}
