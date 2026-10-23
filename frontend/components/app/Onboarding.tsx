/**
 * Onboarding.tsx
 * ==============
 * First-run help for the signed-in app:
 *
 *   FirstRunEmpty  an empty state that does more than say "nothing here" --
 *                  it offers sample starting points the user can click.
 *   GuidedTour     a short, dismissible, step-by-step tour card. Finishing
 *                  or skipping it is remembered in localStorage, so it shows
 *                  once per browser rather than on every visit.
 *
 * The tour is a plain region in the page, not a modal: it never traps focus
 * or blocks the UI it is describing, and it can be ignored entirely.
 */

"use client";

import { Sparkles, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { useT } from "@/lib/i18n";

// ----------------------------------------------------------------------
// Remembering dismissal
// ----------------------------------------------------------------------
const TOUR_PREFIX = "docminds-tour-done:";

export function isTourDone(id: string): boolean {
  try {
    return localStorage.getItem(TOUR_PREFIX + id) === "1";
  } catch {
    return false;
  }
}

export function markTourDone(id: string): void {
  try {
    localStorage.setItem(TOUR_PREFIX + id, "1");
  } catch {
    /* not remembered; it will simply show again next visit */
  }
}

// ----------------------------------------------------------------------
// GuidedTour
// ----------------------------------------------------------------------
export interface TourStep {
  title: string;
  body: string;
}

export function GuidedTour({ id, steps }: { id: string; steps: TourStep[] }) {
  // Hidden until mounted: the server cannot read localStorage, and showing
  // then hiding it would flash for returning users.
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const { t } = useT();

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reading storage after hydration
    setOpen(!isTourDone(id));
  }, [id]);

  if (!open || steps.length === 0) return null;

  function finish() {
    markTourDone(id);
    setOpen(false);
  }

  const last = step === steps.length - 1;
  const current = steps[step];
  const headingId = `tour-${id}-heading`;
  return (
    <section aria-labelledby={headingId} className="glass relative mb-6 p-5">
      <button
        type="button"
        onClick={finish}
        className="absolute right-3 top-3 rounded-lg p-1"
        style={{ color: "var(--text-muted)" }}
        aria-label={t("onboarding.dismiss")}
      >
        <X size={16} aria-hidden="true" />
      </button>
      <p className="mb-1 flex items-center gap-1.5 text-xs font-medium" style={{ color: "var(--accent-hover)" }}>
        <Sparkles size={13} aria-hidden="true" />
        {t("onboarding.step", { step: step + 1, total: steps.length })}
      </p>
      <h2 id={headingId} className="pr-8 font-semibold">
        {current.title}
      </h2>
      <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
        {current.body}
      </p>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        {/* Progress dots: decorative, the text above already says "step 2 of 3". */}
        <div className="flex gap-1.5" aria-hidden="true">
          {steps.map((_, i) => (
            <span
              key={i}
              className="h-1.5 rounded-full transition-all"
              style={{
                width: i === step ? 18 : 6,
                background: i === step ? "var(--accent)" : "var(--border-strong)",
              }}
            />
          ))}
        </div>
        <div className="flex gap-2">
          {!last && (
            <button type="button" className="btn-ghost" onClick={finish}>
              {t("onboarding.skip")}
            </button>
          )}
          <button
            type="button"
            className="btn-primary"
            onClick={() => (last ? finish() : setStep(step + 1))}
          >
            {last ? t("onboarding.gotIt") : t("onboarding.next")}
          </button>
        </div>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// FirstRunEmpty
// ----------------------------------------------------------------------
export function FirstRunEmpty({
  icon,
  title,
  body,
  suggestionsLabel,
  suggestions,
  onSuggestion,
  action,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  /** Heading for the sample list, e.g. "Try one of these". */
  suggestionsLabel?: string;
  suggestions?: string[];
  /** Omit to render the suggestions as plain (non-clickable) examples. */
  onSuggestion?: (s: string) => void;
  action?: ReactNode;
}) {
  return (
    <div className="glass flex flex-col items-center px-6 py-14 text-center">
      <div
        className="blur-reveal mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl"
        style={{
          background: "color-mix(in srgb, var(--accent) 16%, transparent)",
          color: "var(--accent-hover)",
        }}
        aria-hidden="true"
      >
        {icon}
      </div>
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-2 max-w-md text-sm" style={{ color: "var(--text-secondary)" }}>
        {body}
      </p>
      {suggestions && suggestions.length > 0 && (
        <div className="mt-6 w-full max-w-md">
          {suggestionsLabel && (
            <p className="mb-2 text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
              {suggestionsLabel}
            </p>
          )}
          <ul className="flex flex-wrap justify-center gap-2">
            {suggestions.map((s) => (
              <li key={s}>
                {onSuggestion ? (
                  <button type="button" className="btn-ghost text-xs" onClick={() => onSuggestion(s)}>
                    {s}
                  </button>
                ) : (
                  <span className="pill" style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}>
                    {s}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
