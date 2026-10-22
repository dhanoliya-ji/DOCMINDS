/**
 * login/sso/page.tsx  (served at "/login/sso")
 * ============================================
 * Where the backend sends the browser back after "Continue with SSO"
 * (backend/app/api/v1/sso.py). The URL looks like one of:
 *
 *     /login/sso#code=<one-time code>
 *     /login/sso#error=<a message to show>
 *
 * The values are in the FRAGMENT (after #), which the browser never sends to
 * any server -- so the code cannot leak into an access log. This page swaps
 * the code for tokens (lib/sso.ts) and continues to the dashboard, exactly
 * like a password sign-in. The code works once and for two minutes, so it is
 * also wiped from the address bar straight away.
 */

"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AuthCard, Notice } from "@/components/AuthCard";
import { exchangeSsoCode, parseSsoFragment } from "@/lib/sso";

export default function SsoCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  // React's development mode runs effects twice; the code is single-use, so
  // a second exchange would fail and show a false error.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const { code, error: fragmentError } = parseSsoFragment(window.location.hash);
    // Remove the code from the address bar (and so from history).
    window.history.replaceState(null, "", window.location.pathname);

    if (fragmentError || !code) {
      // Deferred a tick: setting state synchronously inside an effect makes
      // React render twice for nothing.
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
      title={error ? "Sign-in failed" : "Signing you in"}
      subtitle={error ? undefined : "Finishing single sign-on..."}
    >
      {error ? (
        <div className="space-y-4">
          <Notice tone="error">{error}</Notice>
          <Link href="/login" className="btn-primary w-full py-3">
            Back to sign in
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
