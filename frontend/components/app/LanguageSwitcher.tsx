// Picks the interface language (lib/i18n): English or Hindi.

"use client";

import { Languages } from "lucide-react";

import { LOCALES, LOCALE_NAMES, type Locale, useT } from "@/lib/i18n";

export default function LanguageSwitcher({ className = "" }: { className?: string }) {
  const { t, locale, setLocale } = useT();
  return (
    <label className={`relative inline-flex items-center ${className}`}>
      <span className="sr-only">{t("shell.language")}</span>
      <Languages
        size={14}
        aria-hidden="true"
        className="pointer-events-none absolute left-2.5"
        style={{ color: "var(--text-muted)" }}
      />
      <select
        value={locale}
        onChange={(e) => setLocale(e.target.value as Locale)}
        className="h-9 cursor-pointer appearance-none rounded-xl border bg-transparent pl-7 pr-2 text-xs"
        style={{
          borderColor: "var(--border-subtle)",
          color: "var(--text-primary)",
          background: "var(--bg-card)",
        }}
      >
        {LOCALES.map((code) => (
          <option key={code} value={code} lang={code}>
            {LOCALE_NAMES[code]}
          </option>
        ))}
      </select>
    </label>
  );
}
