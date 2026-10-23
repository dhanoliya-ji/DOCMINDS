/**
 * QuotaBar.tsx
 * ============
 * One "used of allowed" bar, e.g. 412,000 of 1,000,000 tokens.
 *
 * It is a real <progressbar> for assistive technology (role + aria-value*),
 * not just a coloured div: a screen-reader user asking "how close are we to
 * the limit?" deserves the same answer a sighted one gets from the bar.
 *
 * A quota of 0 means "unlimited" on the backend; then there is nothing to
 * fill, so only the usage is shown.
 */

"use client";

import type { QuotaUsage } from "@/lib/api";
import { useT } from "@/lib/i18n";

export default function QuotaBar({
  label,
  usage,
  format = (n: number) => n.toLocaleString(),
}: {
  label: string;
  usage: QuotaUsage;
  format?: (n: number) => string;
}) {
  const { t } = useT();
  const percent = usage.unlimited || usage.quota <= 0 ? 0 : Math.min(100, (usage.used / usage.quota) * 100);
  // Amber from 80%, red at the limit: warn before the 429/402, not after.
  const colour = percent >= 100 ? "var(--danger)" : percent >= 80 ? "var(--warning)" : "var(--accent)";

  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span style={{ color: "var(--text-secondary)" }}>
          {usage.unlimited
            ? t("quota.noLimit", { used: format(usage.used) })
            : t("quota.of", { used: format(usage.used), quota: format(usage.quota) })}
        </span>
      </div>
      {!usage.unlimited && (
        <div
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
          aria-valuetext={t("quota.percent", { percent: Math.round(percent) })}
          className="h-2 overflow-hidden rounded-full"
          style={{ background: "var(--surface-2)" }}
        >
          <div className="h-full rounded-full" style={{ width: `${percent}%`, background: colour }} />
        </div>
      )}
    </div>
  );
}

/** 5368709120 -> "5.0 GB". */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}
