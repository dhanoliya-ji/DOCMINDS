/**
 * i18n/index.ts
 * =============
 * A deliberately small translation layer: English (the default) and Hindi
 * for the app shell, the auth pages, chat, upload and the ingestion
 * progress line.
 *
 * WHY NOT next-intl
 * -----------------
 * next-intl routes by locale (/en/..., /hi/...) and wants a provider and
 * middleware around every page. This app has a handful of client-rendered
 * screens and one language switcher; a dictionary per area and a hook is
 * the whole requirement, with nothing to configure and nothing for the
 * tests to set up -- a component rendered with no provider at all simply
 * speaks English.
 *
 * HOW A STRING IS LOOKED UP
 * -------------------------
 *     const { t } = useT();
 *     t("upload.dropToUpload")                      -> "Drop to upload"
 *     t("upload.summary", { done: 2, total: 5 })    -> "2 of 5 uploaded"
 *
 *   1. the chosen locale's dictionary;
 *   2. if the key is missing there, ENGLISH -- a half-translated screen is
 *      still a usable one;
 *   3. if English lacks it too, the key itself, so a typo is visible
 *      rather than an empty button.
 *
 * `{name}` placeholders are filled from the second argument.
 *
 * WHERE THE CHOICE LIVES
 * ----------------------
 * localStorage ("docminds-locale"), read through useSyncExternalStore: the
 * server and the hydrating client both render English (getServerSnapshot),
 * then the client switches to the stored language -- no hydration mismatch.
 * Changing it notifies every mounted component and sets <html lang>, which
 * screen readers use to choose a pronunciation.
 *
 * ADDING A STRING: add the key to the area's file (auth.ts, chat.ts...) in
 * `en`, and in `hi` when the translation is known. Missing Hindi is fine.
 */

import { useCallback, useSyncExternalStore } from "react";

import auth from "./auth";
import chat from "./chat";
import platform from "./platform";
import progress from "./progress";
import shell from "./shell";
import upload from "./upload";

export const LOCALES = ["en", "hi"] as const;
export type Locale = (typeof LOCALES)[number];

/** Each language's name in that language, for the switcher. */
export const LOCALE_NAMES: Record<Locale, string> = { en: "English", hi: "हिन्दी" };

export const LOCALE_STORAGE_KEY = "docminds-locale";

type Dictionary = Record<string, string>;
export type AreaMessages = { en: Dictionary; hi: Partial<Dictionary> };

/** Every area's strings, merged per language. */
const MESSAGES: Record<Locale, Dictionary> = { en: {}, hi: {} };
for (const area of [auth, chat, platform, progress, shell, upload] as AreaMessages[]) {
  Object.assign(MESSAGES.en, area.en);
  Object.assign(MESSAGES.hi, area.hi);
}

export type Vars = Record<string, string | number>;

/** Look `key` up in `locale`, falling back to English, then to the key. */
export function translate(locale: Locale, key: string, vars?: Vars): string {
  const template = MESSAGES[locale][key] ?? MESSAGES.en[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match
  );
}

function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** The stored locale, or English. Storage can throw (private mode). */
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
  // Another tab changing the language changes this one too.
  const onStorage = (event: StorageEvent) => event.key === LOCALE_STORAGE_KEY && listener();
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** The current locale and a `t` bound to it. Re-renders on a change. */
export function useT() {
  const locale = useSyncExternalStore(subscribe, readLocale, () => "en" as Locale);
  const t = useCallback((key: string, vars?: Vars) => translate(locale, key, vars), [locale]);
  return { t, locale, setLocale };
}

/** Exposed for the tests: every key in a locale's dictionary. */
export function messageKeys(locale: Locale): string[] {
  return Object.keys(MESSAGES[locale]);
}
