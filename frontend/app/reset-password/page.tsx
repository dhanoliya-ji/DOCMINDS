/**
 * reset-password/page.tsx  (served at "/reset-password?token=...")
 * ================================================================
 * Choose a new password, using the one-time token from the reset email.
 *
 * WHAT THE BACKEND DOES WITH IT
 * -----------------------------
 * POST /auth/password-reset/confirm checks the token (single use, expires
 * after PASSWORD_RESET_TOKEN_EXPIRE_MINUTES), sets the new password, and signs
 * the account out EVERYWHERE -- every existing session was started with the
 * old password, which may be exactly what leaked. So this page does not log
 * the user in; it sends them to sign in with the new password.
 *
 * The link format (`/reset-password?token=`) is set in
 * backend/app/services/email.py; change both sides together.
 */

"use client";

import { Loader2, Lock } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";

import { AuthCard, Notice, PasswordHints } from "@/components/AuthCard";
import { api, getErrorMessage } from "@/lib/api";
import { PASSWORD_MIN_LENGTH, passwordProblems } from "@/lib/password";

// Suspense: required around anything that reads `useSearchParams` (see
// app/login/page.tsx for why).
export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordForm />
    </Suspense>
  );
}

function ResetPasswordForm() {
  const token = useSearchParams().get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");

  // The email is not known on this page, so the "same as your email" rule is
  // left to the backend; its 422 message is shown if it applies.
  const problems = passwordProblems(password);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (problems.length > 0) {
      setError(problems.join(" "));
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      const reply = await api.confirmPasswordReset(token, password);
      setDone(reply.detail || "Your password has been changed. Please sign in with the new one.");
    } catch (err) {
      setError(getErrorMessage(err, "Could not reset the password."));
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="Reset your password">
        <Notice tone="error">
          This link is missing its reset token. Open the link from the email again, or{" "}
          <Link href="/forgot-password" className="underline">
            request a new one
          </Link>
          .
        </Notice>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Choose a new password" subtitle="This also signs you out on every other device.">
      {done ? (
        <div className="space-y-4">
          <Notice tone="success">{done}</Notice>
          <Link href="/login" className="btn-primary w-full py-3">
            Sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <div className="relative">
              <Lock
                size={16}
                aria-hidden="true"
                className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
                style={{ color: "var(--text-muted)" }}
              />
              <input
                type="password"
                aria-label="New password"
                autoComplete="new-password"
                aria-describedby="password-hints"
                className="input pl-11"
                placeholder="New password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {password.length > 0 && (
              <PasswordHints id="password-hints" problems={problems} minLength={PASSWORD_MIN_LENGTH} />
            )}
          </div>
          <div className="relative">
            <Lock
              size={16}
              aria-hidden="true"
              className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
              style={{ color: "var(--text-muted)" }}
            />
            <input
              type="password"
              aria-label="Confirm new password"
              autoComplete="new-password"
              className="input pl-11"
              placeholder="Confirm new password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </div>
          {error && (
            <Notice tone="error">
              {error}{" "}
              {/expired|invalid/i.test(error) && (
                <Link href="/forgot-password" className="underline">
                  Request a new link.
                </Link>
              )}
            </Notice>
          )}
          <button type="submit" disabled={loading} className="btn-primary w-full py-3">
            {loading && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
            {loading ? "Saving..." : "Set new password"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}
