// Page /settings/admin -- the admin dashboard: usage and estimated cost over time, documents processed and...

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import AppShell from "@/components/app/AppShell";
import LogoLoader from "@/components/app/LogoLoader";
import { Notice } from "@/components/AuthCard";
import TrendChart from "@/components/settings/TrendChart";
import { ApiError, getErrorMessage, getToken } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { AnalyticsOverview, platformApi } from "@/lib/platform";

const RANGES = [7, 30, 90] as const;

export default function AdminDashboardPage() {
  const router = useRouter();
  const { t } = useT();
  const [days, setDays] = useState<number>(30);
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) router.push("/login");
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    platformApi
      .getAnalytics(days)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setError("");
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) setError(t("admin.off"));
        else if (err instanceof ApiError && err.status === 403) setError(t("admin.adminsOnly"));
        else setError(getErrorMessage(err, "Could not load the dashboard."));
      });
    return () => {
      cancelled = true;
    };
  }, [days, t]);

  const totals = (data?.totals ?? {}) as Record<string, number | null>;
  const usd = (n: number) => `$${n.toFixed(n < 1 ? 4 : 2)}`;

  return (
    <AppShell>
      <Link href="/settings" className="mb-4 inline-block text-sm" style={{ color: "var(--text-secondary)" }}>
        ← {t("platform.back")}
      </Link>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-gradient text-3xl font-bold">{t("admin.title")}</h1>
        <label className="flex items-center gap-2 text-sm">
          <span>{t("admin.range")}</span>
          <select
            className="input w-auto"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {RANGES.map((d) => (
              <option key={d} value={d}>
                {t("admin.days", { days: d })}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <Notice tone="error">{error}</Notice>
      ) : !data ? (
        <LogoLoader label={t("platform.loading")} />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <Stat label={t("admin.requests")} value={(totals.requests ?? 0).toLocaleString()} />
            <Stat label={t("admin.tokens")} value={(totals.tokens ?? 0).toLocaleString()} />
            <Stat label={t("admin.cost")} value={usd(totals.cost_usd ?? 0)} />
            <Stat label={t("admin.activeUsers")} value={(totals.active_users ?? 0).toLocaleString()} />
            <Stat label={t("admin.processed")} value={(totals.documents_processed ?? 0).toLocaleString()} />
            <Stat label={t("admin.failed")} value={(totals.documents_failed ?? 0).toLocaleString()} />
            <Stat
              label={t("admin.satisfaction")}
              value={totals.satisfaction == null ? "—" : `${Math.round(totals.satisfaction * 100)}%`}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <TrendChart
              title={t("admin.chart.usage")}
              rows={data.usage}
              series={[{ key: "requests", label: t("admin.requests"), colour: "var(--accent)" }]}
              dateLabel={t("admin.date")}
              tableLabel={t("admin.showTable")}
            />
            <TrendChart
              title={t("admin.chart.cost")}
              rows={data.usage}
              series={[{ key: "cost_usd", label: t("admin.cost"), colour: "var(--violet)" }]}
              format={usd}
              dateLabel={t("admin.date")}
              tableLabel={t("admin.showTable")}
            />
            <TrendChart
              title={t("admin.chart.documents")}
              rows={data.documents}
              series={[
                { key: "processed", label: t("admin.series.processed"), colour: "var(--accent)" },
                { key: "failed", label: t("admin.series.failed"), colour: "var(--danger)" },
              ]}
              dateLabel={t("admin.date")}
              tableLabel={t("admin.showTable")}
            />
            <TrendChart
              title={t("admin.chart.users")}
              rows={data.active_users}
              series={[{ key: "active_users", label: t("admin.activeUsers"), colour: "var(--cyan)" }]}
              dateLabel={t("admin.date")}
              tableLabel={t("admin.showTable")}
            />
            <TrendChart
              title={t("admin.chart.feedback")}
              rows={data.feedback}
              series={[
                { key: "up", label: t("admin.series.up"), colour: "var(--accent)" },
                { key: "down", label: t("admin.series.down"), colour: "var(--warning)" },
              ]}
              dateLabel={t("admin.date")}
              tableLabel={t("admin.showTable")}
            />
          </div>

          <section className="glass p-5" aria-labelledby="top-questions">
            <h2 id="top-questions" className="mb-3 text-lg font-semibold">
              {t("admin.topQuestions")}
            </h2>
            {data.top_questions.length === 0 ? (
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                {t("admin.noQuestions")}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">{t("admin.topQuestions")}</caption>
                  <thead style={{ color: "var(--text-muted)" }}>
                    <tr>
                      <th scope="col" className="py-2 font-medium">
                        {t("admin.question")}
                      </th>
                      <th scope="col" className="py-2 text-right font-medium">
                        {t("admin.count")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_questions.map((q) => (
                      <tr key={q.question} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                        <td className="break-words py-2 pr-4">{q.question}</td>
                        <td className="py-2 text-right">{q.count.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </AppShell>
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
