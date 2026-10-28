// Page /reset-password?token=... -- choose a new password, using the one-time token from the reset email.

"use client";

import { Loader2, Lock } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";

import { AuthCard, Notice, PasswordHints } from "@/components/AuthCard";
import { api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PASSWORD_MIN_LENGTH, passwordProblems } from "@/lib/password";

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordForm />
    </Suspense>
  );
}

function ResetPasswordForm() {
  const { t } = useT();
  const token = useSearchParams().get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");

  const problems = passwordProblems(password);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (problems.length > 0) {
      setError(problems.join(" "));
      return;
    }
    if (password !== confirm) {
      setError(t("auth.mismatch"));
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
      <AuthCard title={t("auth.resetTitle")}>
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
    <AuthCard title={t("auth.chooseNew")} subtitle={t("auth.chooseNewSubtitle")}>
      {done ? (
        <div className="space-y-4">
          <Notice tone="success">{done}</Notice>
          <Link href="/login" className="btn-primary w-full py-3">
            {t("auth.signIn")}
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
                aria-label={t("auth.newPassword")}
                autoComplete="new-password"
                aria-describedby="password-hints"
                className="input pl-11"
                placeholder={t("auth.newPassword")}
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
              aria-label={t("auth.confirmPassword")}
              autoComplete="new-password"
              className="input pl-11"
              placeholder={t("auth.confirmPassword")}
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
                  {t("auth.requestNewLink")}
                </Link>
              )}
            </Notice>
          )}
          <button type="submit" disabled={loading} className="btn-primary w-full py-3">
            {loading && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
            {loading ? t("auth.saving") : t("auth.setPassword")}
          </button>
        </form>
      )}
    </AuthCard>
  );
}
