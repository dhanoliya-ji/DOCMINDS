/**
 * verify-email/page.tsx  (served at "/verify-email?token=...")
 * ============================================================
 * Confirms an email address using the link from the verification email.
 *
 * NO BUTTON TO PRESS
 * ------------------
 * Clicking the emailed link already says "yes, this is my address", so the
 * page sends the token as soon as it opens and just reports the result.
 *
 * WHY A MODULE-LEVEL CACHE OF REQUESTS
 * ------------------------------------
 * The token is SINGLE USE. In development React deliberately runs every effect
 * twice (Strict Mode) to flush out bugs; two POSTs would mean the second one
 * fails with "invalid or expired" and the page would show an error for a
 * verification that actually succeeded. Remembering the promise per token
 * makes the second run reuse the first request instead of sending another.
 *
 * The link format (`/verify-email?token=`) is set in
 * backend/app/services/email.py; change both sides together.
 */

"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AuthCard, Notice } from "@/components/AuthCard";
import { api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

const requests = new Map<string, Promise<{ detail: string }>>();

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={null}>
      <VerifyEmail />
    </Suspense>
  );
}

function VerifyEmail() {
  const { t } = useT();
  const token = useSearchParams().get("token") || "";
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    let request = requests.get(token);
    if (!request) {
      request = api.verifyEmail(token);
      requests.set(token, request);
    }
    request
      .then((reply) => {
        if (!cancelled)
          setResult({ ok: true, message: reply.detail || "Your email address has been confirmed." });
      })
      .catch((err) => {
        if (!cancelled)
          setResult({ ok: false, message: getErrorMessage(err, "This verification link did not work.") });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);


  return (
    <AuthCard title={t("auth.confirmEmail")}>
      <div className="space-y-4">
        {!token ? (
          <Notice tone="error">This link is missing its verification token. Open the link from the email again.</Notice>
        ) : result === null ? (
          <p role="status" className="flex items-center justify-center gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
            <Loader2 size={16} className="animate-spin" aria-hidden="true" /> {t("auth.confirming")}
          </p>
        ) : (
          <Notice tone={result.ok ? "success" : "error"}>
            {result.message}
            {!result.ok && " You can ask for a new link from the banner on your projects page."}
          </Notice>
        )}
        {/* The dashboard sends a signed-out visitor on to /login, so one link
            serves both cases -- and reading the token during render would
            differ between the server and the browser (a hydration mismatch). */}
        <Link href="/dashboard" className="btn-primary w-full py-3">
          {t("auth.continue")}
        </Link>
      </div>
    </AuthCard>
  );
}
