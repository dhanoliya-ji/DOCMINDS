/**
 * I18n.test.tsx
 * =============
 * The translation layer (lib/i18n) and the language switcher:
 *
 *   * translate: placeholders, the English fallback for a key Hindi lacks,
 *     the key itself for a key nobody has
 *   * the dictionaries: every Hindi key also exists in English (no orphans)
 *   * the switcher: picking Hindi re-renders the shell and the auth pages,
 *     sets <html lang>, and is remembered
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AppShell from "@/components/app/AppShell";
import LanguageSwitcher from "@/components/app/LanguageSwitcher";
import ForgotPasswordPage from "@/app/forgot-password/page";
import { LOCALE_STORAGE_KEY, messageKeys, readLocale, setLocale, translate } from "@/lib/i18n";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, logout: vi.fn(), requestPasswordReset: vi.fn() } };
});

describe("translate", () => {
  it("fills placeholders", () => {
    expect(translate("en", "upload.summary", { done: 2, total: 5 })).toBe("2 of 5 uploaded");
    expect(translate("hi", "upload.summary", { done: 2, total: 5 })).toBe("5 में से 2 अपलोड हुईं");
  });

  it("falls back to English for a key Hindi lacks, then to the key", () => {
    const englishOnly = messageKeys("en").find((k) => !messageKeys("hi").includes(k));
    if (englishOnly) expect(translate("hi", englishOnly)).toBe(translate("en", englishOnly));
    expect(translate("hi", "no.such.key")).toBe("no.such.key");
  });

  it("has no Hindi key without an English original", () => {
    const english = new Set(messageKeys("en"));
    expect(messageKeys("hi").filter((k) => !english.has(k))).toEqual([]);
  });

  it("ignores an unknown stored locale", () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, "xx");
    expect(readLocale()).toBe("en");
  });
});

describe("language switcher", () => {
  it("switches the app shell to Hindi and back, remembering the choice", async () => {
    const user = userEvent.setup();
    render(
      <AppShell>
        <p>content</p>
      </AppShell>
    );
    expect(screen.getAllByText("Projects").length).toBeGreaterThan(0);

    await user.selectOptions(screen.getAllByLabelText("Language")[0], "hi");
    expect(screen.getAllByText("प्रोजेक्ट").length).toBeGreaterThan(0);
    expect(screen.queryByText("Projects")).not.toBeInTheDocument();
    expect(document.documentElement.lang).toBe("hi");
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("hi");

    act(() => setLocale("en"));
    expect(screen.getAllByText("Projects").length).toBeGreaterThan(0);
  });

  it("translates the auth pages, where signed-out visitors can switch too", async () => {
    const user = userEvent.setup();
    render(<ForgotPasswordPage />);
    expect(screen.getByText("Reset your password")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Language"), "hi");
    expect(screen.getByText("अपना पासवर्ड रीसेट करें")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "रीसेट लिंक भेजें" })).toBeInTheDocument();
    act(() => setLocale("en"));
  });

  it("names each language in its own script", () => {
    render(<LanguageSwitcher />);
    expect(screen.getByRole("option", { name: "हिन्दी" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "English" })).toBeInTheDocument();
  });
});
