/**
 * analytics.ts
 * ============
 * Optional, consent-gated product analytics with PostHog.
 *
 * THREE GATES, ALL REQUIRED
 * -------------------------
 *   1. NEXT_PUBLIC_POSTHOG_KEY is set. Without it this whole file does
 *      nothing, and the PostHog library is never even downloaded (it is
 *      loaded with a dynamic `import()` only after gate 3).
 *   2. The visitor has not been asked yet -> the consent banner
 *      (components/AnalyticsConsent.tsx) asks.
 *   3. The visitor clicked "Allow". "Decline" is remembered just the same,
 *      and nothing is ever sent.
 *
 * NO PERSONAL DATA
 * ----------------
 * DocMinds never calls `identify`, so PostHog sees an anonymous random id,
 * not an email or a user id. On top of that:
 *   - autocapture is OFF: it records the text of clicked elements, which in
 *     this app can be a document name or a chat question.
 *   - session recording is OFF: it would film documents and answers.
 *   - every URL is stripped to its PATH before it is sent. Query strings and
 *     fragments here carry secrets: ?invite=, ?token= (password reset,
 *     email verification) and #code= (single sign-on).
 *   - IP addresses are not stored with events (`ip: false`).
 * What remains is "a page view of /dashboard", which is the point.
 */

const KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY || "";
const HOST = process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com";

/** The localStorage key remembering the visitor's choice. */
export const CONSENT_KEY = "docminds_analytics_consent";
export type Consent = "granted" | "denied";

export function analyticsConfigured(): boolean {
  return KEY.length > 0;
}

export function getConsent(): Consent | null {
  if (typeof window === "undefined") return null;
  try {
    const value = window.localStorage.getItem(CONSENT_KEY);
    return value === "granted" || value === "denied" ? value : null;
  } catch {
    return null; // storage blocked: treat as "not asked", and never send
  }
}

export function setConsent(value: Consent): void {
  try {
    window.localStorage.setItem(CONSENT_KEY, value);
  } catch {
    // Storage blocked: the banner will simply ask again next visit.
  }
}

/** "https://app/x/y?invite=abc#code=1" -> "https://app/x/y". */
export function stripUrl(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    const url = new URL(value);
    return `${url.origin}${url.pathname}`;
  } catch {
    return value.split(/[?#]/)[0];
  }
}

// Every property that can hold a full URL.
const URL_PROPERTIES = ["$current_url", "$referrer", "$initial_referrer", "$pathname"];

/** Applied to every event just before it leaves the browser. */
export function sanitizeProperties(properties: Record<string, unknown>): Record<string, unknown> {
  const cleaned = { ...properties };
  for (const name of URL_PROPERTIES) {
    if (name in cleaned) cleaned[name] = stripUrl(cleaned[name]);
  }
  return cleaned;
}

let started = false;

/**
 * Load and start PostHog -- only with a key AND consent. Safe to call more
 * than once; only the first call does anything.
 */
export async function startAnalytics(): Promise<boolean> {
  if (started || !analyticsConfigured() || getConsent() !== "granted") return false;
  started = true;
  const { default: posthog } = await import("posthog-js");
  posthog.init(KEY, {
    api_host: HOST,
    autocapture: false,
    capture_pageview: true,
    capture_pageleave: true,
    disable_session_recording: true,
    disable_surveys: true,
    // Anonymous visitors only: no person profiles are ever created.
    person_profiles: "identified_only",
    ip: false,
    // Keep its id in localStorage, not a cookie sent with every request.
    persistence: "localStorage",
    sanitize_properties: (properties) => sanitizeProperties(properties),
  });
  return true;
}

/** Stop sending (used when a visitor withdraws consent). */
export async function stopAnalytics(): Promise<void> {
  if (!started) return;
  const { default: posthog } = await import("posthog-js");
  posthog.opt_out_capturing();
}
