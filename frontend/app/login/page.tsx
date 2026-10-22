/**
 * login/page.tsx  (served at "/login")
 * ====================================
 * One screen that handles signing in, creating an account, and accepting an
 * invite, toggled by a little state. Keeping them together avoids duplicating
 * the form, the validation and the error handling across nearly identical
 * files.
 *
 * WHAT HAPPENS ON SUBMIT
 * ----------------------
 *   Sign in : POST /auth/login  -> receive tokens -> save them -> dashboard
 *   Sign up : POST /auth/signup -> creates a NEW org with the user as Admin
 *                                -> immediately log in with the same details
 *   Invite  : POST /auth/signup with `invite_token` -> joins the inviting org
 *             with the role the Admin chose -> log in
 *
 * WHY INVITES LIVE HERE AND NOT ON THEIR OWN PAGE
 * -----------------------------------------------
 * The invite email (backend/app/services/email.py) links to
 * `/login?invite=<token>`. Accepting an invite IS signing up -- same fields,
 * same request, one extra parameter -- so it is a mode of this form rather
 * than a copy of it. The link format is the contract; change both sides
 * together.
 *
 * Signing up WITHOUT an invite always creates a brand-new organization. There
 * is deliberately no way to type in someone else's org: joining one requires
 * an invite from its Admin. That is what keeps tenants isolated.
 */

"use client";

import { motion } from "framer-motion";
import { ArrowRight, Building2, Loader2, Lock, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";

import { AuthCard, Notice, PasswordHints } from "@/components/AuthCard";
import { ApiError, InvitePreview, api, getErrorMessage } from "@/lib/api";
import { PASSWORD_MIN_LENGTH, passwordProblems } from "@/lib/password";
import { roleLabel } from "@/lib/roles";

/**
 * `useSearchParams` makes a page depend on the request's query string, which
 * Next.js cannot know while pre-rendering. It therefore requires a Suspense
 * boundary around whatever reads it -- without one, `next build` fails. The
 * exported page is just that boundary.
 */
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

  // ---- Form state ----------------------------------------------------
  // Each input is a "controlled component": React holds the value in state and
  // the input displays it. An invite link opens straight into signup mode.
  const [isSignup, setIsSignup] = useState(Boolean(inviteToken));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");

  // ---- Invite state --------------------------------------------------
  // What the invite is for ("Join Acme as Editor"), or why it cannot be used.
  const [invite, setInvite] = useState<InvitePreview | null>(null);
  const [inviteError, setInviteError] = useState("");

  // ---- Request state -------------------------------------------------
  // `loading` disables the button and shows a spinner, which prevents the user
  // double-submitting and creating two accounts.
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!inviteToken) return;
    let cancelled = false;
    api
      .previewInvite(inviteToken)
      .then((preview) => {
        if (cancelled) return;
        setInvite(preview);
        // The invite is bound to one address, and the backend refuses any
        // other, so fill it in and lock the field.
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

  // Only use the token once the backend has confirmed it is usable.
  const joining = isSignup && invite !== null;
  const problems = passwordProblems(password, email);
  const hintsId = "password-hints";

  async function handleSubmit(event: FormEvent) {
    // Stop the browser's default behaviour of reloading the page on submit,
    // which would throw away all our React state.
    event.preventDefault();
    setError("");
    setNotice("");

    // Check the password policy before the round trip; the backend checks
    // again regardless.
    if (isSignup && problems.length > 0) {
      setError(problems.join(" "));
      return;
    }

    setLoading(true);
    try {
      if (isSignup) {
        await api.signup(email, password, orgName, joining ? inviteToken : undefined);
        try {
          // Log straight in, so the user is not asked to type the same
          // credentials again immediately.
          await api.login(email, password);
        } catch (err) {
          // With REQUIRE_EMAIL_VERIFICATION on, the account exists but cannot
          // sign in until the emailed link is clicked. That is success, not
          // failure, so say so and switch to the sign-in form.
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

      // `push` navigates without a full page reload, so the app stays fast.
      router.push("/dashboard");
    } catch (err) {
      // Show the backend's real message ("A user with this email already
      // exists", "Too many sign-in attempts...") rather than a generic one.
      setError(getErrorMessage(err, "Something went wrong. Please try again."));
    } finally {
      // `finally` runs whether or not an error was thrown, so the button can
      // never get permanently stuck in its loading state.
      setLoading(false);
    }
  }

  const title = joining
    ? `Join ${invite.org_name}`
    : isSignup
      ? "Create your workspace"
      : "Welcome back";
  const subtitle = joining
    ? `You've been invited as ${roleLabel(invite.role)}. Choose a password to finish.`
    : isSignup
      ? "Set up an organization and start uploading documents."
      : "Sign in to search and question your documents.";

  return (
    <AuthCard title={title} subtitle={subtitle}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {inviteError && isSignup && <Notice tone="error">{inviteError}</Notice>}

        {/* Organization name: only when creating a NEW organization. Someone
            accepting an invite joins the inviter's organization instead. */}
        {isSignup && !joining && (
          <motion.div
            // Animating the height lets the field slide open smoothly rather
            // than making the whole form jump when it appears.
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
              // aria-label because the visible cue is the placeholder, which
              // screen readers do not reliably announce as a label.
              aria-label="Organization name"
              placeholder="Organization name"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              required
              maxLength={255}
            />
          </motion.div>
        )}

        {/* Email */}
        <div className="relative">
          <Mail
            size={16}
            aria-hidden="true"
            className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
            style={{ color: "var(--text-muted)" }}
          />
          <input
            // type="email" makes mobile browsers show an @-friendly keyboard
            // and gives basic format validation for free.
            type="email"
            aria-label="Email"
            autoComplete="email"
            className="input pl-11"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            readOnly={joining}
            required
          />
        </div>

        {/* Password */}
        <div className="space-y-2">
          <div className="relative">
            <Lock
              size={16}
              aria-hidden="true"
              className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2"
              style={{ color: "var(--text-muted)" }}
            />
            <input
              // type="password" masks the characters as they are typed.
              type="password"
              aria-label="Password"
              // Tells password managers whether to offer a saved password or
              // to generate a new one.
              autoComplete={isSignup ? "new-password" : "current-password"}
              aria-describedby={isSignup ? hintsId : undefined}
              className="input pl-11"
              placeholder="Password"
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
              Forgot password?
            </Link>
          </div>
        )}

        {notice && <Notice tone="success">{notice}</Notice>}
        {error && <Notice tone="error">{error}</Notice>}

        <button
          type="submit"
          // Disabling during the request is what prevents a double submit.
          disabled={loading}
          className="btn-primary w-full py-3"
        >
          {loading ? (
            <>
              <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              {isSignup ? "Creating account..." : "Signing in..."}
            </>
          ) : (
            <>
              {joining ? "Join workspace" : isSignup ? "Create workspace" : "Sign in"}
              <ArrowRight size={16} aria-hidden="true" />
            </>
          )}
        </button>
      </form>

      {/* Toggle between the two modes */}
      <div className="mt-6 text-center text-sm">
        <span style={{ color: "var(--text-muted)" }}>
          {isSignup ? "Already have an account?" : "No account yet?"}
        </span>{" "}
        <button
          type="button"
          onClick={() => {
            setIsSignup(!isSignup);
            // Clear any stale message so one from the previous mode does not
            // linger confusingly over the new form.
            setError("");
            setNotice("");
          }}
          className="font-medium transition-colors"
          style={{ color: "var(--accent-hover)" }}
        >
          {isSignup ? "Sign in" : "Create one"}
        </button>
      </div>
    </AuthCard>
  );
}
