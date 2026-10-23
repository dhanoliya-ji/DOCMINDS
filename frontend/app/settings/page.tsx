/**
 * settings/page.tsx  (served at "/settings")
 * ==========================================
 * The organization's admin area: Members, API keys, Audit log, Usage and
 * Feedback (how people rate the answers).
 *
 * WHO SEES WHAT
 * -------------
 * Every /org endpoint requires the admin role, so only an Admin gets the tabs.
 * Anyone else sees their own account details and a sentence explaining that
 * these settings belong to their admin -- rather than four tabs that would
 * each fail with 403. (The backend enforces this regardless; hiding the tabs
 * is about not offering what will be refused.)
 *
 * TABS AND ACCESSIBILITY
 * ----------------------
 * The tab strip follows the WAI-ARIA tabs pattern: role="tablist"/"tab"/
 * "tabpanel", aria-selected on the active tab, and the panel labelled by its
 * tab, so a screen reader announces "Members, tab, 1 of 4, selected". Left
 * and right arrow keys move between tabs, as the pattern expects.
 *
 * The Webhooks tab appears only when the deployment has webhooks switched
 * on: GET /webhooks is probed once for an admin, and a 404 (the feature is
 * off) leaves the tab out entirely rather than showing a dead screen.
 *
 * Wrapped in <AppShell>, which provides navigation and sign-out; destructive
 * actions in the panels use <HoldToConfirm> rather than window.confirm().
 */

"use client";

import { useRouter } from "next/navigation";
import { KeyboardEvent, useEffect, useRef, useState } from "react";

import AppShell from "@/components/app/AppShell";
import LogoLoader from "@/components/app/LogoLoader";
import { Notice } from "@/components/AuthCard";
import ApiKeysPanel from "@/components/settings/ApiKeysPanel";
import AuditLogPanel from "@/components/settings/AuditLogPanel";
import FeedbackPanel from "@/components/settings/FeedbackPanel";
import MembersPanel from "@/components/settings/MembersPanel";
import UsagePanel from "@/components/settings/UsagePanel";
import WebhooksPanel, { webhooksAvailable } from "@/components/settings/WebhooksPanel";
import VerifyBanner from "@/components/VerifyBanner";
import { getToken } from "@/lib/api";
import { rich, useT } from "@/lib/i18n";
import { isAdmin, roleLabel } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

// Each tab's label is the i18n key `settings.tab.<id>`.
const TABS = [
  { id: "members" },
  { id: "api-keys" },
  { id: "audit" },
  { id: "usage" },
  { id: "feedback" },
  { id: "webhooks" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function SettingsPage() {
  const router = useRouter();
  const { t } = useT();
  const { user, loading, error } = useCurrentUser();
  const [tab, setTab] = useState<TabId>("members");
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  // Whether the Webhooks tab exists: unknown (false) until the probe answers.
  const [webhooksOn, setWebhooksOn] = useState(false);
  const admin = !!user && isAdmin(user.role);
  const tabs = TABS.filter((tab) => tab.id !== "webhooks" || webhooksOn);

  useEffect(() => {
    if (!admin) return;
    let cancelled = false;
    webhooksAvailable().then((on) => {
      if (!cancelled) setWebhooksOn(on);
    });
    return () => {
      cancelled = true;
    };
  }, [admin]);

  // Same guard as the other signed-in pages.
  useEffect(() => {
    if (!getToken()) router.push("/login");
  }, [router]);

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const index = tabs.findIndex((item) => item.id === tab);
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(index + step + tabs.length) % tabs.length].id;
    setTab(next);
    tabRefs.current[next]?.focus();
  }

  return (
    <AppShell>
      <h1 className="text-gradient mb-8 text-3xl font-bold">{t("settings.title")}</h1>

      {loading ? (
        <LogoLoader label={t("settings.loading")} />
      ) : error || !user ? (
        <Notice tone="error">{error || t("settings.loadError")}</Notice>
      ) : (
        <>
          {!user.is_verified && <VerifyBanner email={user.email} />}

          {/* ---- Your account: shown to everyone ---- */}
          <section className="glass mb-8 p-5 text-sm" aria-label={t("settings.account")}>
            <div className="break-words">
              {rich(t("settings.signedInAs"), {
                email: <strong>{user.email}</strong>,
                role: roleLabel(user.role),
              })}
            </div>
            {!isAdmin(user.role) && (
              <p className="mt-2" style={{ color: "var(--text-secondary)" }}>
                {t("settings.notAdmin")}
              </p>
            )}
          </section>

          {isAdmin(user.role) && (
            <>
              <div role="tablist" aria-label={t("settings.sections")} className="mb-6 flex flex-wrap gap-2">
                {tabs.map((item) => (
                  <button
                    key={item.id}
                    ref={(el) => {
                      tabRefs.current[item.id] = el;
                    }}
                    id={`tab-${item.id}`}
                    role="tab"
                    type="button"
                    aria-selected={tab === item.id}
                    aria-controls={`panel-${item.id}`}
                    // Roving tabindex: only the active tab is in the Tab order;
                    // arrows move between the others.
                    tabIndex={tab === item.id ? 0 : -1}
                    onKeyDown={onTabKey}
                    onClick={() => setTab(item.id)}
                    className={tab === item.id ? "btn-primary" : "btn-ghost"}
                  >
                    {t(`settings.tab.${item.id}`)}
                  </button>
                ))}
              </div>
              {/* overflow-x-auto: the member and key tables are wider than a
                  phone, so they scroll inside the panel, never the page. */}
              <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="min-w-0 overflow-x-auto">
                {tab === "members" && <MembersPanel currentUserId={user.id} />}
                {tab === "api-keys" && <ApiKeysPanel />}
                {tab === "audit" && <AuditLogPanel />}
                {tab === "usage" && <UsagePanel />}
                {tab === "feedback" && <FeedbackPanel />}
                {tab === "webhooks" && webhooksOn && <WebhooksPanel />}
              </div>
            </>
          )}
        </>
      )}
    </AppShell>
  );
}
