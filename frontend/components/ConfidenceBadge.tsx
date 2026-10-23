/**
 * ConfidenceBadge.tsx
 * ===================
 * "High / Medium / Low confidence" under an answer, and -- one click away --
 * the reasons, so a rating is never an unexplained label.
 *
 * The verdict is computed by the backend from measurements (how closely the
 * excerpts matched, how much of the answer cites a real source --
 * services/confidence.py), never by asking the model how sure it is. Each
 * reason arrives as a code plus a number and is worded here, so it can be
 * translated.
 *
 * The level is carried by the word and the icon, not colour alone.
 */

"use client";

import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useId, useState } from "react";

import type { Confidence } from "@/lib/api";
import { useT } from "@/lib/i18n";

const STYLE = {
  high: { icon: ShieldCheck, color: "var(--success)" },
  medium: { icon: ShieldQuestion, color: "var(--warning)" },
  low: { icon: ShieldAlert, color: "var(--danger)" },
} as const;

function reasonValue(code: string, value?: number): string | number | undefined {
  if (value === undefined) return undefined;
  // Similarities and coverage are 0..1: shown as a percentage.
  if (/_match$|_cited$|^few_citations$/.test(code)) return `${Math.round(value * 100)}%`;
  return value;
}

export default function ConfidenceBadge({ confidence }: { confidence?: Confidence | null }) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (!confidence || !(confidence.level in STYLE)) return null;
  const { icon: Icon, color } = STYLE[confidence.level];

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        className="flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
        style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={listId}
        title={t("confidence.why")}
      >
        <Icon size={12} aria-hidden="true" />
        {t(`confidence.${confidence.level}`)}
      </button>
      {open && (
        <ul
          id={listId}
          className="glass absolute left-0 top-7 z-20 w-72 space-y-1 p-3 text-xs"
          style={{ color: "var(--text-secondary)" }}
        >
          {confidence.reasons.map((reason) => (
            <li key={reason.code}>
              {t(`confidence.reason.${reason.code}`, {
                value: reasonValue(reason.code, reason.value) ?? "",
              })}
            </li>
          ))}
        </ul>
      )}
    </span>
  );
}
