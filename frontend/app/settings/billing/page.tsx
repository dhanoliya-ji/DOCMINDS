/**
 * settings/billing/page.tsx  (served at "/settings/billing")
 * ===========================================================
 * The upgrade page: the organization's plan (Free / Pro / Team), what each
 * plan allows, and two ways out to Stripe:
 *
 *   * "Upgrade to Pro" -> POST /billing/checkout -> Stripe Checkout
 *   * "Manage billing" -> POST /billing/portal   -> Stripe Customer Portal
 *     (card, invoices, switching plan, cancelling)
 *
 * Coming back from Checkout, ?checkout=success shows a thank-you -- but the
 * plan shown is always what the API says, which changes only when Stripe's
 * signed webhook arrives (services/billing.py). Everyone may look; only
 * admins get the buttons (the API refuses anyone else regardless).
 */

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import AppShell from "@/components/app/AppShell";
import LogoLoader from "@/components/app/LogoLoader";
import { Notice } from "@/components/AuthCard";
import { formatBytes } from "@/components/settings/QuotaBar";
import { ApiError, getErrorMessage, getToken } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { BillingStatus, Plan, navigate, platformApi } from "@/lib/platform";
import { isAdmin } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

export default function BillingPage() {
  return (
    <Suspense fallback={null}>
      <Billing />
    </Suspense>
  );
}

function Billing() {
  const router = useRouter();
  const params = useSearchParams();
  const { t } = useT();
  const { user } = useCurrentUser();
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const admin = !!user && isAdmin(user.role);
  const checkout = params.get("checkout");

  useEffect(() => {
    if (!getToken()) router.push("/login");
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    platformApi
      .getBilling()
      .then((s) => !cancelled && setStatus(s))
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? t("billing.off")
            : getErrorMessage(err, "Could not load billing.")
        );
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  async function go(call: () => Promise<{ url: string }>) {
    setBusy(true);
    setError("");
    try {
      const { url } = await call();
      navigate.to(url);
    } catch (err) {
      setError(getErrorMessage(err, "Could not open Stripe."));
      setBusy(false);
    }
  }

  const planName = (id: string) => t(`billing.plan.${id}`);
  const limit = (value: number, format: (n: number) => string = (n) => n.toLocaleString()) =>
    value === 0 ? t("billing.unlimited") : format(value);

  return (
    <AppShell>
      <Link href="/settings" className="mb-4 inline-block text-sm" style={{ color: "var(--text-secondary)" }}>
        ← {t("platform.back")}
      </Link>
      <h1 className="text-gradient mb-6 text-3xl font-bold">{t("billing.title")}</h1>

      {checkout === "success" && <Notice tone="success">{t("billing.success")}</Notice>}
      {checkout === "cancelled" && <Notice tone="info">{t("billing.cancelled")}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}

      {!status ? (
        !error && <LogoLoader label={t("platform.loading")} />
      ) : (
        <div className="mt-6 space-y-6">
          <section className="glass flex flex-wrap items-center justify-between gap-4 p-5" aria-label={t("billing.current")}>
            <div>
              <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                {t("billing.current")}
              </div>
              <div className="mt-1 text-2xl font-semibold">{planName(status.plan)}</div>
              {status.subscription_status && (
                <div className="text-sm" style={{ color: "var(--text-secondary)" }}>
                  {t("billing.status", { status: status.subscription_status })}
                </div>
              )}
              {status.renews_at && (
                <div className="text-sm" style={{ color: "var(--text-secondary)" }}>
                  {t("billing.renews", { date: new Date(status.renews_at).toLocaleDateString() })}
                </div>
              )}
            </div>
            {admin && status.has_customer && (
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => go(platformApi.openPortal)}>
                {busy ? t("billing.redirecting") : t("billing.manage")}
              </button>
            )}
          </section>

          {!admin && (
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              {t("billing.adminsOnly")}
            </p>
          )}

          <ul className="grid gap-4 md:grid-cols-3">
            {status.plans.map((plan: Plan) => {
              const current = plan.id === status.plan;
              return (
                <li
                  key={plan.id}
                  className="glass flex flex-col gap-3 p-5"
                  aria-current={current ? "true" : undefined}
                  style={current ? { outline: "2px solid var(--accent)" } : undefined}
                >
                  <h2 className="text-lg font-semibold">{planName(plan.id)}</h2>
                  <div className="text-xl">{plan.price_label}</div>
                  <ul className="space-y-1 text-sm" style={{ color: "var(--text-secondary)" }}>
                    <li>{t("billing.tokens", { value: limit(plan.limits.monthly_tokens) })}</li>
                    <li>{t("billing.documents", { value: limit(plan.limits.max_documents) })}</li>
                    <li>{t("billing.storage", { value: limit(plan.limits.max_storage_bytes, formatBytes) })}</li>
                  </ul>
                  <div className="mt-auto">
                    {current ? (
                      <span className="text-sm font-medium">{t("billing.yourPlan")}</span>
                    ) : admin && plan.purchasable && (plan.id === "pro" || plan.id === "team") ? (
                      <button
                        type="button"
                        className="btn-primary w-full"
                        disabled={busy}
                        onClick={() =>
                          go(() =>
                            status.has_customer && status.plan !== "free"
                              ? platformApi.openPortal()
                              : platformApi.checkout(plan.id as "pro" | "team")
                          )
                        }
                      >
                        {status.plan === "free"
                          ? t("billing.upgrade", { plan: planName(plan.id) })
                          : t("billing.switch", { plan: planName(plan.id) })}
                      </button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </AppShell>
  );
}
