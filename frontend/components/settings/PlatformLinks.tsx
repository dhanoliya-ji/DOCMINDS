/**
 * PlatformLinks.tsx
 * =================
 * Two small pieces for the settings page, for the optional features that
 * live on pages of their own:
 *
 *   <PlanBadge />      "Pro plan", linking to /settings/billing -- shown to
 *                      every member, and only when billing is switched on
 *   <PlatformLinks />  links to Cloud imports (editors and admins), Plan and
 *                      billing, and the Admin dashboard (admins), each only
 *                      when the server has that feature on
 *
 * Each feature's endpoint answers 404 while it is off, so one probe per
 * feature decides whether its link appears; nothing renders until then.
 */

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useT } from "@/lib/i18n";
import { BillingStatus, analyticsAvailable, integrationsAvailable, platformApi } from "@/lib/platform";
import { canEdit, isAdmin } from "@/lib/roles";

export function PlanBadge() {
  const { t } = useT();
  const [billing, setBilling] = useState<BillingStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    platformApi
      .getBilling()
      .then((b) => !cancelled && setBilling(b))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!billing) return null;
  return (
    <Link
      href="/settings/billing"
      className="ml-2 inline-block rounded-full px-2.5 py-0.5 text-xs font-medium"
      style={{ background: "var(--surface-2)", color: "var(--text-primary)", border: "1px solid var(--border-strong)" }}
    >
      {t("billing.badge", { plan: t(`billing.plan.${billing.plan}`) })}
    </Link>
  );
}

export default function PlatformLinks({ role }: { role: string }) {
  const { t } = useT();
  const [features, setFeatures] = useState({ integrations: false, billing: false, admin: false });

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      canEdit(role) ? integrationsAvailable() : Promise.resolve(false),
      platformApi.getBilling().then(() => true, () => false),
      isAdmin(role) ? analyticsAvailable() : Promise.resolve(false),
    ]).then(([integrations, billing, admin]) => {
      if (!cancelled) setFeatures({ integrations, billing, admin });
    });
    return () => {
      cancelled = true;
    };
  }, [role]);

  const links = [
    features.integrations && { href: "/settings/integrations", label: t("integrations.link") },
    features.billing && { href: "/settings/billing", label: t("billing.link") },
    features.admin && { href: "/settings/admin", label: t("admin.link") },
  ].filter(Boolean) as { href: string; label: string }[];

  if (links.length === 0) return null;
  return (
    <nav aria-label={t("platform.links")} className="mb-8 flex flex-wrap gap-2">
      {links.map((link) => (
        <Link key={link.href} href={link.href} className="btn-ghost">
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
