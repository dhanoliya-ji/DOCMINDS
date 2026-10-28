// Page /login/sso -- where the backend sends the browser back after "Continue with SSO"...

"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AuthCard, Notice } from "@/components/AuthCard";
import { useT } from "@/lib/i18n";
import { exchangeSsoCode, parseSsoFragment } from "@/lib/sso";

export default function SsoCallbackPage() {
  const { t } = useT();
  const router = useRouter();
  const [error, setError] = useState("");
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const { code, error: fragmentError } = parseSsoFragment(window.location.hash);
    window.history.replaceState(null, "", window.location.pathname);

    if (fragmentError || !code) {
      queueMicrotask(() =>
        setError(fragmentError || "The sign-in response was incomplete. Please try again.")
      );
      return;
    }
    exchangeSsoCode(code)
      .then(() => router.push("/dashboard"))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Sign-in failed. Please try again.")
      );
  }, [router]);

  return (
    <AuthCard
      title={error ? t("auth.ssoFailed") : t("auth.ssoSigningIn")}
      subtitle={error ? undefined : t("auth.ssoFinishing")}
    >
      {error ? (
        <div className="space-y-4">
          <Notice tone="error">{error}</Notice>
          <Link href="/login" className="btn-primary w-full py-3">
            {t("auth.backToSignIn")}
          </Link>
        </div>
      ) : (
        <div className="flex justify-center py-6" role="status" aria-label="Signing in">
          <Loader2 size={24} className="animate-spin" aria-hidden="true" />
        </div>
      )}
    </AuthCard>
  );
}
