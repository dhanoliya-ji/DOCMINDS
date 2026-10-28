// Page /verify-email?token=... -- confirms an email address using the link from the verification email.

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
        <Link href="/dashboard" className="btn-primary w-full py-3">
          {t("auth.continue")}
        </Link>
      </div>
    </AuthCard>
  );
}
