/**
 * AnalyticsConsent.tsx
 * ====================
 * The small "may we count page views?" banner, and the switch that starts
 * PostHog once the visitor says yes (lib/analytics.ts).
 *
 * Renders NOTHING at all when NEXT_PUBLIC_POSTHOG_KEY is not set -- the
 * default -- so a deployment without analytics never shows the banner.
 * Once answered, the choice is remembered and the banner does not return.
 */

"use client";

import { BarChart3 } from "lucide-react";
import { useEffect, useState } from "react";

import {
  Consent,
  analyticsConfigured,
  getConsent,
  setConsent,
  startAnalytics,
  stopAnalytics,
} from "@/lib/analytics";

export default function AnalyticsConsent() {
  // null until mounted: localStorage does not exist during server rendering,
  // and rendering the banner there would flash it at people who already
  // answered.
  const [choice, setChoice] = useState<Consent | null | "unknown">("unknown");

  useEffect(() => {
    if (!analyticsConfigured()) return;
    const saved = getConsent();
    queueMicrotask(() => setChoice(saved));
    if (saved === "granted") void startAnalytics();
  }, []);

  if (!analyticsConfigured() || choice !== null) return null;

  function answer(value: Consent) {
    setConsent(value);
    setChoice(value);
    if (value === "granted") void startAnalytics();
    else void stopAnalytics();
  }

  return (
    <div
      role="dialog"
      aria-label="Analytics consent"
      className="glass fixed inset-x-4 bottom-4 z-50 mx-auto flex max-w-xl flex-col gap-3 rounded-2xl p-4 text-sm sm:flex-row sm:items-center"
    >
      <BarChart3 size={18} aria-hidden="true" className="shrink-0" />
      <p className="flex-1" style={{ color: "var(--text-muted)" }}>
        May we count anonymous page views to improve DocMinds? No documents,
        questions, names or emails are ever sent.
      </p>
      <div className="flex gap-2">
        <button type="button" className="btn-ghost px-3 py-2" onClick={() => answer("denied")}>
          Decline
        </button>
        <button type="button" className="btn-primary px-3 py-2" onClick={() => answer("granted")}>
          Allow
        </button>
      </div>
    </div>
  );
}
