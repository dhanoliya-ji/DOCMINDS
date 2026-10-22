/**
 * SsoAnalytics.test.tsx
 * =====================
 * The two optional features of the signed-out shell:
 *
 *   SINGLE SIGN-ON   the "Continue with SSO" button appears only when the
 *                    backend says SSO is on, carries an invite along, and the
 *                    /login/sso page swaps the #code for tokens or shows the
 *                    #error it was given.
 *   ANALYTICS        without NEXT_PUBLIC_POSTHOG_KEY nothing renders and
 *                    nothing starts; URLs are stripped of their query and
 *                    fragment (they carry invite, reset and SSO secrets).
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";
import SsoCallbackPage from "@/app/login/sso/page";
import AnalyticsConsent from "@/components/AnalyticsConsent";
import { analyticsConfigured, sanitizeProperties, startAnalytics, stripUrl } from "@/lib/analytics";
import { pushMock, searchParamsMock } from "./setup";

const sso = vi.hoisted(() => ({
  getSsoConfig: vi.fn(),
  exchangeSsoCode: vi.fn(),
}));

vi.mock("@/lib/sso", async () => {
  const actual = await vi.importActual<typeof import("@/lib/sso")>("@/lib/sso");
  return { ...actual, ...sso };
});

beforeEach(() => {
  sso.getSsoConfig.mockReset();
  sso.exchangeSsoCode.mockReset();
});

describe("Continue with SSO", () => {
  it("is hidden while SSO is off", async () => {
    sso.getSsoConfig.mockResolvedValue({ enabled: false, provider_name: "SSO" });
    render(<LoginPage />);
    await waitFor(() => expect(sso.getSsoConfig).toHaveBeenCalled());
    expect(screen.queryByText(/Continue with/)).toBeNull();
  });

  it("links to the API's SSO login, labelled with the provider", async () => {
    sso.getSsoConfig.mockResolvedValue({ enabled: true, provider_name: "Okta" });
    render(<LoginPage />);
    const link = await screen.findByRole("link", { name: /Continue with Okta/ });
    expect(link.getAttribute("href")).toMatch(/\/api\/v1\/auth\/sso\/login$/);
  });
});

describe("/login/sso", () => {
  // tests/setup.ts swaps window.location for a plain object, so the fragment
  // is set the same way, and history.replaceState is watched rather than run.
  let replaceState: ReturnType<typeof vi.spyOn>;
  function arriveWith(hash: string) {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: { ...window.location, pathname: "/login/sso", hash },
    });
    replaceState = vi.spyOn(window.history, "replaceState").mockImplementation(() => {});
  }
  afterEach(() => {
    replaceState?.mockRestore();
  });

  it("exchanges the code, wipes it from the URL and goes to the dashboard", async () => {
    sso.exchangeSsoCode.mockResolvedValue(undefined);
    arriveWith("#code=one-time");
    render(<SsoCallbackPage />);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(sso.exchangeSsoCode).toHaveBeenCalledWith("one-time");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/login/sso");
  });

  it("shows the error the backend sent", async () => {
    arriveWith("#error=Your%20email%20is%20not%20verified.");
    render(<SsoCallbackPage />);
    expect(await screen.findByText("Your email is not verified.")).toBeTruthy();
    expect(sso.exchangeSsoCode).not.toHaveBeenCalled();
  });

  it("shows why an exchange failed", async () => {
    sso.exchangeSsoCode.mockRejectedValue(new Error("This sign-in code is invalid or has expired."));
    arriveWith("#code=used");
    render(<SsoCallbackPage />);
    expect(await screen.findByText(/invalid or has expired/)).toBeTruthy();
  });
});

describe("analytics", () => {
  it("is off without a key: no banner, nothing started", async () => {
    expect(analyticsConfigured()).toBe(false);
    const { container } = render(<AnalyticsConsent />);
    expect(container.innerHTML).toBe("");
    expect(await startAnalytics()).toBe(false);
  });

  it("strips query strings and fragments, which carry secrets", () => {
    expect(stripUrl("https://app.example.com/login?invite=secret#code=x")).toBe(
      "https://app.example.com/login"
    );
    const cleaned = sanitizeProperties({
      $current_url: "https://app.example.com/reset-password?token=abc",
      $referrer: "https://app.example.com/login/sso#code=xyz",
      other: "kept?as-is",
    });
    expect(cleaned.$current_url).toBe("https://app.example.com/reset-password");
    expect(cleaned.$referrer).toBe("https://app.example.com/login/sso");
    expect(cleaned.other).toBe("kept?as-is");
  });
});

// Keep the shared search-params mock in its default state for other files.
afterEach(() => {
  searchParamsMock.value = new URLSearchParams();
});
