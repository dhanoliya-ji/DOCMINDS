/**
 * AppShell.tsx
 * ============
 * The chrome around every signed-in page: a sidebar on desktop, a topbar
 * with a slide-in drawer on phones, the theme toggle, sign-out, and the
 * animated page transition (F13).
 *
 * LAYOUT
 * ------
 *   >= md (768px)   [ sidebar 15rem ][ topbar / content ]
 *   <  md           [ topbar with menu button ]
 *                   [ content                 ]
 *
 * Everything inside uses `min-w-0` so a long project name or a wide table
 * cannot push the page sideways -- at 375px there must be no horizontal
 * scroll, which a single over-wide child is enough to cause.
 *
 * PAGE TRANSITIONS (F13)
 * ----------------------
 * The content area fades and rises 8px over 220ms, keyed on the path, so
 * every navigation between dashboard, project and settings feels continuous.
 * Short on purpose (<300ms): a transition the user waits on is a delay.
 * Under prefers-reduced-motion it is skipped entirely.
 */

"use client";

import { motion } from "framer-motion";
import { FolderOpen, LogOut, Menu, Settings, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import ThemeToggle from "@/components/app/ThemeToggle";
import { usePrefersReducedMotion } from "@/components/landing/hooks";
import { api } from "@/lib/api";

const NAV = [
  { href: "/dashboard", label: "Projects", icon: FolderOpen },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

export default function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const reduced = usePrefersReducedMotion();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Escape closes the drawer, as it would any overlay.
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

  // The project page is "under" Projects, so that item stays highlighted there.
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
              {label}
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
        Skip to content
      </a>

      {/* ---- Desktop sidebar ---- */}
      <aside
        className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r p-4 md:flex"
        style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}
      >
        <div className="mb-8 px-2">{brand}</div>
        <nav aria-label="Main" className="flex-1">
          {navList}
        </nav>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <button type="button" onClick={handleLogout} className="btn-ghost flex-1" aria-label="Sign out">
            <LogOut size={16} aria-hidden="true" />
            <span>Sign out</span>
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* ---- Mobile topbar ---- */}
        <header
          className="sticky top-0 z-30 flex items-center justify-between gap-2 border-b px-4 py-3 backdrop-blur-xl md:hidden"
          style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}
        >
          <button
            type="button"
            className="btn-ghost px-2.5"
            aria-label="Open menu"
            aria-expanded={drawerOpen}
            aria-controls="app-drawer"
            onClick={() => setDrawerOpen(true)}
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          <div className="flex min-w-0 flex-1 justify-center">{brand}</div>
          <ThemeToggle />
        </header>

        {/* ---- Mobile drawer ---- */}
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
              aria-label="Menu"
              className="absolute inset-y-0 left-0 flex w-64 max-w-[80vw] flex-col p-4"
              style={{ background: "var(--bg-elevated)" }}
            >
              <div className="mb-6 flex items-center justify-between">
                {brand}
                <button
                  type="button"
                  className="btn-ghost px-2.5"
                  aria-label="Close menu"
                  onClick={() => setDrawerOpen(false)}
                  // Move focus into the drawer when it opens.
                  autoFocus
                >
                  <X size={18} aria-hidden="true" />
                </button>
              </div>
              <nav aria-label="Main" className="flex-1">
                {navList}
              </nav>
              <button type="button" onClick={handleLogout} className="btn-ghost" aria-label="Sign out">
                <LogOut size={16} aria-hidden="true" />
                <span>Sign out</span>
              </button>
            </div>
          </div>
        )}

        {/* ---- Page content, with the F13 transition ---- */}
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
