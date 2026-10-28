// The chrome around every signed-in page: a sidebar on desktop, a topbar with a slide-in...

"use client";

import { motion } from "framer-motion";
import { FolderOpen, LogOut, Menu, Settings, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import LanguageSwitcher from "@/components/app/LanguageSwitcher";
import ThemeToggle from "@/components/app/ThemeToggle";
import { usePrefersReducedMotion } from "@/components/landing/hooks";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

const NAV = [
  { href: "/dashboard", label: "shell.nav.projects", icon: FolderOpen },
  { href: "/settings", label: "shell.nav.settings", icon: Settings },
] as const;

export default function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const reduced = usePrefersReducedMotion();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { t } = useT();

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  function handleLogout() {
    api.logout();
    router.push("/login");
  }

  const isActive = (href: string) =>
    pathname === href || (href === "/dashboard" && pathname.startsWith("/project"));

  const navList = (
    <ul className="space-y-1">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = isActive(href);
        return (
          <li key={href}>
            <Link
              href={href}
              onClick={() => setDrawerOpen(false)}
              aria-current={active ? "page" : undefined}
              className="flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-colors"
              style={{
                color: active ? "var(--text-primary)" : "var(--text-secondary)",
                background: active ? "var(--surface-2)" : "transparent",
              }}
            >
              <Icon size={16} aria-hidden="true" />
              {t(label)}
            </Link>
          </li>
        );
      })}
    </ul>
  );

  const brand = (
    <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
      <span
        aria-hidden="true"
        className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-xs font-bold text-white"
        style={{ background: "linear-gradient(135deg, var(--accent), var(--violet))" }}
      >
        D
      </span>
      DocMinds
    </Link>
  );

  return (
    <div className="app-shell flex min-h-screen w-full overflow-x-clip">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:px-3 focus:py-2"
        style={{ background: "var(--bg-elevated)" }}
      >
        {t("shell.skip")}
      </a>

      <aside
        className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r p-4 md:flex"
        style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}
      >
        <div className="mb-8 px-2">{brand}</div>
        <nav aria-label={t("shell.nav.main")} className="flex-1">
          {navList}
        </nav>
        <LanguageSwitcher className="mb-2" />
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <button type="button" onClick={handleLogout} className="btn-ghost flex-1" aria-label={t("shell.signOut")}>
            <LogOut size={16} aria-hidden="true" />
            <span>{t("shell.signOut")}</span>
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="sticky top-0 z-30 flex items-center justify-between gap-2 border-b px-4 py-3 backdrop-blur-xl md:hidden"
          style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}
        >
          <button
            type="button"
            className="btn-ghost px-2.5"
            aria-label={t("shell.openMenu")}
            aria-expanded={drawerOpen}
            aria-controls="app-drawer"
            onClick={() => setDrawerOpen(true)}
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          <div className="flex min-w-0 flex-1 justify-center">{brand}</div>
          <ThemeToggle />
        </header>

        {drawerOpen && (
          <div className="fixed inset-0 z-40 md:hidden">
            <div
              className="absolute inset-0"
              style={{ background: "var(--overlay)" }}
              onClick={() => setDrawerOpen(false)}
              aria-hidden="true"
            />
            <div
              id="app-drawer"
              role="dialog"
              aria-modal="true"
              aria-label={t("shell.menu")}
              className="absolute inset-y-0 left-0 flex w-64 max-w-[80vw] flex-col p-4"
              style={{ background: "var(--bg-elevated)" }}
            >
              <div className="mb-6 flex items-center justify-between">
                {brand}
                <button
                  type="button"
                  className="btn-ghost px-2.5"
                  aria-label={t("shell.closeMenu")}
                  onClick={() => setDrawerOpen(false)}
                  autoFocus
                >
                  <X size={18} aria-hidden="true" />
                </button>
              </div>
              <nav aria-label={t("shell.nav.main")} className="flex-1">
                {navList}
              </nav>
              <LanguageSwitcher className="mb-2" />
              <button type="button" onClick={handleLogout} className="btn-ghost" aria-label={t("shell.signOut")}>
                <LogOut size={16} aria-hidden="true" />
                <span>{t("shell.signOut")}</span>
              </button>
            </div>
          </div>
        )}

        <motion.main
          id="main"
          key={pathname}
          initial={reduced ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reduced ? 0 : 0.22, ease: [0.2, 0.8, 0.2, 1] }}
          className="mx-auto w-full min-w-0 max-w-6xl flex-1 px-4 py-6 sm:px-6 md:py-10"
        >
          {children}
        </motion.main>
      </div>
    </div>
  );
}
