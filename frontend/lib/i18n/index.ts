// A deliberately small translation layer: English (the default) and Hindi for every...

import { Fragment, type ReactNode, createElement, useCallback, useSyncExternalStore } from "react";

import auth from "./auth";
import chat from "./chat";
import collab from "./collab";
import dashboard from "./dashboard";
import developers from "./developers";
import landing from "./landing";
import platform from "./platform";
import progress from "./progress";
import project from "./project";
import search from "./search";
import settings from "./settings";
import shell from "./shell";
import upload from "./upload";

export const LOCALES = ["en", "hi"] as const;
export type Locale = (typeof LOCALES)[number];

export const LOCALE_NAMES: Record<Locale, string> = { en: "English", hi: "हिन्दी" };

export const LOCALE_STORAGE_KEY = "docminds-locale";

type Dictionary = Record<string, string>;
export type AreaMessages = { en: Dictionary; hi: Partial<Dictionary> };

const MESSAGES: Record<Locale, Dictionary> = { en: {}, hi: {} };
for (const area of [auth, chat, collab, dashboard, developers, landing, platform, progress, project, search, settings, shell, upload] as AreaMessages[]) {
  Object.assign(MESSAGES.en, area.en);
  Object.assign(MESSAGES.hi, area.hi);
}

export type Vars = Record<string, string | number>;

export function translate(locale: Locale, key: string, vars?: Vars): string {
  const template = MESSAGES[locale][key] ?? MESSAGES.en[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match
  );
}

export function rich(template: string, parts: Record<string, ReactNode>): ReactNode[] {
  return template
    .split(/(\{\w+\})/g)
    .filter((piece) => piece !== "")
    .map((piece, index) => {
      const slot = /^\{(\w+)\}$/.exec(piece);
      const node = slot && slot[1] in parts ? parts[slot[1]] : piece;
      return createElement(Fragment, { key: index }, node);
    });
}

function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

export function readLocale(): Locale {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(stored) ? stored : "en";
  } catch {
    return "en";
  }
}

const listeners = new Set<() => void>();

export function setLocale(locale: Locale): void {
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Not remembered; still applied for this page view below.
  }
  if (typeof document !== "undefined") document.documentElement.lang = locale;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => event.key === LOCALE_STORAGE_KEY && listener();
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useT() {
  const locale = useSyncExternalStore(subscribe, readLocale, () => "en" as Locale);
  const t = useCallback((key: string, vars?: Vars) => translate(locale, key, vars), [locale]);
  return { t, locale, setLocale };
}

export function messageKeys(locale: Locale): string[] {
  return Object.keys(MESSAGES[locale]);
}
