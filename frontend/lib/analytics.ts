// Optional, consent-gated product analytics with PostHog.

const KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY || "";
const HOST = process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com";

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
    return null;
  }
}

export function setConsent(value: Consent): void {
  try {
    window.localStorage.setItem(CONSENT_KEY, value);
  } catch {
    // Storage blocked: the banner will simply ask again next visit.
  }
}

export function stripUrl(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    const url = new URL(value);
    return `${url.origin}${url.pathname}`;
  } catch {
    return value.split(/[?#]/)[0];
  }
}

const URL_PROPERTIES = ["$current_url", "$referrer", "$initial_referrer", "$pathname"];

export function sanitizeProperties(properties: Record<string, unknown>): Record<string, unknown> {
  const cleaned = { ...properties };
  for (const name of URL_PROPERTIES) {
    if (name in cleaned) cleaned[name] = stripUrl(cleaned[name]);
  }
  return cleaned;
}

let started = false;

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
    person_profiles: "identified_only",
    ip: false,
    persistence: "localStorage",
    sanitize_properties: (properties) => sanitizeProperties(properties),
  });
  return true;
}

export async function stopAnalytics(): Promise<void> {
  if (!started) return;
  const { default: posthog } = await import("posthog-js");
  posthog.opt_out_capturing();
}
