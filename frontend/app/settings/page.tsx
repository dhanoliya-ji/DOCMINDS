/**
 * settings/page.tsx  (served at "/settings")
 * ==========================================
 * The organization's admin area: Members, API keys, Audit log and Usage.
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
 */

"use client";

import { ArrowLeft, LogOut } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { KeyboardEvent, useEffect, useRef, useState } from "react";

import { Notice } from "@/components/AuthCard";
import ApiKeysPanel from "@/components/settings/ApiKeysPanel";
import AuditLogPanel from "@/components/settings/AuditLogPanel";
import MembersPanel from "@/components/settings/MembersPanel";
import UsagePanel from "@/components/settings/UsagePanel";
import VerifyBanner from "@/components/VerifyBanner";
import { api, getToken } from "@/lib/api";
import { isAdmin, roleLabel } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

const TABS = [
  { id: "members", label: "Members" },
  { id: "api-keys", label: "API keys" },
  { id: "audit", label: "Audit log" },
  { id: "usage", label: "Usage" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function SettingsPage() {
  const router = useRouter();
  const { user, loading, error } = useCurrentUser();
  const [tab, setTab] = useState<TabId>("members");
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  // Same guard as the other signed-in pages.
  useEffect(() => {
    if (!getToken()) router.push("/login");
  }, [router]);

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const index = TABS.findIndex((t) => t.id === tab);
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = TABS[(index + step + TABS.length) % TABS.length].id;
    setTab(next);
    tabRefs.current[next]?.focus();
  }

  function handleLogout() {
    api.logout();
    router.push("/login");
  }

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-6 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <Link
            href="/dashboard"
            className="mb-2 inline-flex items-center gap-1 text-sm"
            style={{ color: "var(--text-secondary)" }}
          >
            <ArrowLeft size={14} aria-hidden="true" /> Projects
          </Link>
          <h1 className="text-gradient text-3xl font-bold">Settings</h1>
        </div>
        <button onClick={handleLogout} className="btn-ghost" title="Sign out" aria-label="Sign out">
          <LogOut size={16} aria-hidden="true" />
        </button>
      </div>

      {loading ? (
        <div className="glass shimmer h-32" aria-busy="true" />
      ) : error || !user ? (
        <Notice tone="error">{error || "Could not load your account."}</Notice>
      ) : (
        <>
          {!user.is_verified && <VerifyBanner email={user.email} />}

          {/* ---- Your account: shown to everyone ---- */}
          <section className="glass mb-8 p-5 text-sm" aria-label="Your account">
            <div>
              Signed in as <strong>{user.email}</strong> · {roleLabel(user.role)}
            </div>
            {!isAdmin(user.role) && (
              <p className="mt-2" style={{ color: "var(--text-secondary)" }}>
                Members, API keys, the audit log and usage are managed by your
                organization&apos;s admins.
              </p>
            )}
          </section>

          {isAdmin(user.role) && (
            <>
              <div role="tablist" aria-label="Settings sections" className="mb-6 flex flex-wrap gap-2">
                {TABS.map((t) => (
                  <button
                    key={t.id}
                    ref={(el) => {
                      tabRefs.current[t.id] = el;
                    }}
                    id={`tab-${t.id}`}
                    role="tab"
                    type="button"
                    aria-selected={tab === t.id}
                    aria-controls={`panel-${t.id}`}
                    // Roving tabindex: only the active tab is in the Tab order;
                    // arrows move between the others.
                    tabIndex={tab === t.id ? 0 : -1}
                    onKeyDown={onTabKey}
                    onClick={() => setTab(t.id)}
                    className={tab === t.id ? "btn-primary" : "btn-ghost"}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
                {tab === "members" && <MembersPanel currentUserId={user.id} />}
                {tab === "api-keys" && <ApiKeysPanel />}
                {tab === "audit" && <AuditLogPanel />}
                {tab === "usage" && <UsagePanel />}
              </div>
            </>
          )}
        </>
      )}
    </main>
  );
}
