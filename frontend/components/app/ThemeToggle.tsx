/**
 * ThemeToggle.tsx
 * ===============
 * Cycles the theme: system -> light -> dark -> system.
 *
 * One button rather than a three-way menu: it fits the topbar at 375px, and
 * its accessible name always says what it is set to AND what a press does,
 * so a screen-reader user is never guessing.
 *
 * The initial render uses "system" on both server and client (so hydration
 * matches), then reads the stored choice after mount. The inline head script
 * has already painted the right colours, so this causes no visible change.
 */

"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { applyTheme, readThemeChoice, writeThemeChoice, type ThemeChoice } from "@/lib/theme";

const NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<ThemeChoice, string> = { system: "System", light: "Light", dark: "Dark" };

export default function ThemeToggle() {
  const [choice, setChoice] = useState<ThemeChoice>("system");

  useEffect(() => {
    const stored = readThemeChoice();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing from storage after hydration
    setChoice(stored);
    applyTheme(stored);
  }, []);

  // While on "system", follow the OS live.
  useEffect(() => {
    if (choice !== "system" || !window.matchMedia) return;
    const mql = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => applyTheme("system");
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, [choice]);

  function cycle() {
    const next = NEXT[choice];
    setChoice(next);
    writeThemeChoice(next);
    applyTheme(next);
  }

  const Icon = choice === "light" ? Sun : choice === "dark" ? Moon : Monitor;
  return (
    <button
      type="button"
      onClick={cycle}
      className="btn-ghost px-2.5"
      aria-label={`Theme: ${LABEL[choice]}. Switch to ${LABEL[NEXT[choice]]}`}
      title={`Theme: ${LABEL[choice]}`}
    >
      <Icon size={16} aria-hidden="true" />
    </button>
  );
}
