// Page /settings -- the organization's admin area: Members, API keys, Audit log, Usage and Feedback (how...

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
import PlatformLinks, { PlanBadge } from "@/components/settings/PlatformLinks";
import UsagePanel from "@/components/settings/UsagePanel";
import WebhooksPanel, { webhooksAvailable } from "@/components/settings/WebhooksPanel";
import VerifyBanner from "@/components/VerifyBanner";
import { getToken } from "@/lib/api";
import { rich, useT } from "@/lib/i18n";
import { isAdmin, roleLabel } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

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

          <section className="glass mb-8 p-5 text-sm" aria-label={t("settings.account")}>
            <div className="break-words">
              {rich(t("settings.signedInAs"), {
                email: <strong>{user.email}</strong>,
                role: roleLabel(user.role),
              })}
              <PlanBadge />
            </div>
            {!isAdmin(user.role) && (
              <p className="mt-2" style={{ color: "var(--text-secondary)" }}>
                {t("settings.notAdmin")}
              </p>
            )}
          </section>

          <PlatformLinks role={user.role} />

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
                    tabIndex={tab === item.id ? 0 : -1}
                    onKeyDown={onTabKey}
                    onClick={() => setTab(item.id)}
                    className={tab === item.id ? "btn-primary" : "btn-ghost"}
                  >
                    {t(`settings.tab.${item.id}`)}
                  </button>
                ))}
              </div>
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
