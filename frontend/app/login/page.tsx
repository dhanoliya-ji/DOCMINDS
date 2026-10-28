// Page /login -- one screen that handles signing in, creating an account, and accepting an invite...

"use client";

import { motion } from "framer-motion";
import { ArrowRight, Building2, KeyRound, Loader2, Lock, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";

import { AuthCard, Notice, PasswordHints } from "@/components/AuthCard";
import { ApiError, InvitePreview, api, getErrorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PASSWORD_MIN_LENGTH, passwordProblems } from "@/lib/password";
import { roleLabel } from "@/lib/roles";
import { SsoConfig, getSsoConfig, ssoLoginUrl } from "@/lib/sso";

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const inviteToken = useSearchParams().get("invite") || "";

  const [isSignup, setIsSignup] = useState(Boolean(inviteToken));
  const { t } = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");

  const [invite, setInvite] = useState<InvitePreview | null>(null);
  const [inviteError, setInviteError] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [sso, setSso] = useState<SsoConfig | null>(null);
  useEffect(() => {
    let cancelled = false;
    getSsoConfig().then((config) => {
      if (!cancelled) setSso(config);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!inviteToken) return;
    let cancelled = false;
    api
      .previewInvite(inviteToken)
      .then((preview) => {
        if (cancelled) return;
        setInvite(preview);
        setEmail(preview.email);
      })
      .catch((err) => {
        if (!cancelled) {
          setInviteError(
            getErrorMessage(err, "This invite link is not valid.") +
              " Ask your admin for a new invite, or create your own workspace below."
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [inviteToken]);

  const joining = isSignup && invite !== null;
  const problems = passwordProblems(password, email);
  const hintsId = "password-hints";

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");

    if (isSignup && problems.length > 0) {
      setError(problems.join(" "));
      return;
    }

    setLoading(true);
    try {
      if (isSignup) {
        await api.signup(email, password, orgName, joining ? inviteToken : undefined);
        try {
          await api.login(email, password);
        } catch (err) {
          if (err instanceof ApiError && err.status === 403) {
            setIsSignup(false);
            setNotice(
              "Account created. We've emailed you a link to confirm your address -- " +
                "click it, then sign in here."
            );
            return;
          }
          throw err;
        }
      } else {
        await api.login(email, password);
      }

      router.push("/dashboard");
    } catch (err) {
      setError(getErrorMessage(err, t("auth.genericError")));
    } finally {
      setLoading(false);
    }
  }

  const title = joining
    ? t("auth.joinTitle", { org: invite.org_name })
    : isSignup
      ? t("auth.createWorkspaceTitle")
      : t("auth.welcomeBack");
  const subtitle = joining
    ? t("auth.joinSubtitle", { role: roleLabel(invite.role) })
    : isSignup
      ? t("auth.signupSubtitle")
      : t("auth.signInSubtitle");

  return (
    <AuthCard title={title} subtitle={subtitle}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {inviteError && isSignup && <Notice tone="error">{inviteError}</Notice>}

        {isSignup && !joining && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            className="relative"
          >
            <Building2
              size={16}
              aria-hidden="true"
              className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
              style={{ color: "var(--text-muted)" }}
            />
            <input
              className="input pl-11"
              aria-label={t("auth.orgName")}
              placeholder={t("auth.orgName")}
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              required
              maxLength={255}
            />
          </motion.div>
        )}

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
            readOnly={joining}
            required
          />
        </div>

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
              aria-label={t("auth.password")}
              autoComplete={isSignup ? "new-password" : "current-password"}
              aria-describedby={isSignup ? hintsId : undefined}
              className="input pl-11"
              placeholder={t("auth.password")}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {isSignup && password.length > 0 && (
            <PasswordHints id={hintsId} problems={problems} minLength={PASSWORD_MIN_LENGTH} />
          )}
        </div>

        {!isSignup && (
          <div className="text-right text-sm">
            <Link href="/forgot-password" style={{ color: "var(--accent-hover)" }}>
              {t("auth.forgot")}
            </Link>
          </div>
        )}

        {notice && <Notice tone="success">{notice}</Notice>}
        {error && <Notice tone="error">{error}</Notice>}

        <button
          type="submit"
          disabled={loading}
          className="btn-primary w-full py-3"
        >
          {loading ? (
            <>
              <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              {isSignup ? t("auth.creatingAccount") : t("auth.signingIn")}
            </>
          ) : (
            <>
              {joining ? t("auth.joinWorkspace") : isSignup ? t("auth.createWorkspace") : t("auth.signIn")}
              <ArrowRight size={16} aria-hidden="true" />
            </>
          )}
        </button>
      </form>

      {sso?.enabled && (
        <div className="mt-4">
          <a href={ssoLoginUrl(joining ? inviteToken : undefined)} className="btn-ghost w-full py-3">
            <KeyRound size={16} aria-hidden="true" />
            {t("auth.continueWith", { provider: sso.provider_name })}
          </a>
        </div>
      )}

      <div className="mt-6 text-center text-sm">
        <span style={{ color: "var(--text-muted)" }}>
          {isSignup ? t("auth.haveAccount") : t("auth.noAccount")}
        </span>{" "}
        <button
          type="button"
          onClick={() => {
            setIsSignup(!isSignup);
            setError("");
            setNotice("");
          }}
          className="font-medium transition-colors"
          style={{ color: "var(--accent-hover)" }}
        >
          {isSignup ? t("auth.signIn") : t("auth.createOne")}
        </button>
      </div>
    </AuthCard>
  );
}
