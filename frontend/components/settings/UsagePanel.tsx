/**
 * UsagePanel.tsx
 * ==============
 * This month's AI usage and the organization's quotas (GET /org/usage).
 *
 * WHY SHOW IT
 * -----------
 * When the monthly token quota runs out, chat starts answering 429 until the
 * 1st of next month. An admin should see that coming -- the bar turns amber at
 * 80% -- rather than discover it from a colleague's error message. The cost
 * is an ESTIMATE from LLM_PRICING; the provider's invoice is the real figure.
 */

"use client";

import { useEffect, useState } from "react";

import { Notice } from "@/components/AuthCard";
import QuotaBar, { formatBytes } from "@/components/settings/QuotaBar";
import { UsageSummary, api, getErrorMessage } from "@/lib/api";

export default function UsagePanel() {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .getUsage()
      .then((u) => !cancelled && setUsage(u))
      .catch((err) => !cancelled && setError(getErrorMessage(err, "Could not load usage.")));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!usage) return <div className="glass shimmer h-40" aria-busy="true" />;

  const period = `${new Date(usage.period_start).toLocaleDateString()} – ${new Date(
    usage.period_end
  ).toLocaleDateString()}`;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Questions answered" value={usage.requests.toLocaleString()} />
        <Stat
          label="Tokens in / out"
          value={`${usage.prompt_tokens.toLocaleString()} / ${usage.completion_tokens.toLocaleString()}`}
        />
        <Stat label="Estimated cost" value={`$${usage.estimated_cost_usd.toFixed(4)}`} />
      </div>
      <div className="glass space-y-5 p-5">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Billing period {period}
        </p>
        <QuotaBar label="AI tokens this month" usage={usage.tokens} />
        <QuotaBar label="Documents" usage={usage.documents} />
        <QuotaBar label="Storage" usage={usage.storage_bytes} format={formatBytes} />
      </div>
      {usage.by_model.length > 0 && (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Usage by model</caption>
          <thead style={{ color: "var(--text-muted)" }}>
            <tr>
              <th className="py-2 font-medium">Model</th>
              <th className="py-2 font-medium">Requests</th>
              <th className="py-2 font-medium">Tokens</th>
              <th className="py-2 font-medium">Est. cost</th>
            </tr>
          </thead>
          <tbody>
            {usage.by_model.map((m) => (
              <tr key={m.model} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                <td className="py-2">{m.model}</td>
                <td className="py-2">{m.requests.toLocaleString()}</td>
                <td className="py-2">{m.total_tokens.toLocaleString()}</td>
                <td className="py-2">${m.estimated_cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="glass p-4">
      <div className="text-xs" style={{ color: "var(--text-muted)" }}>
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}
