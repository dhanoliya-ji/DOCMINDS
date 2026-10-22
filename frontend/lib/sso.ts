/**
 * sso.ts
 * ======
 * The three calls behind "Continue with SSO" (backend/app/api/v1/sso.py).
 * Kept apart from lib/api.ts so single sign-on can be read -- and removed --
 * as one piece.
 *
 * THE FLOW, FROM THE BROWSER'S SIDE
 * ---------------------------------
 *   1. /login asks `getSsoConfig()` whether SSO is switched on, and shows the
 *      button only if it is.
 *   2. The button is a plain link to `ssoLoginUrl()`: a full-page navigation
 *      to the API, which redirects on to Google / Microsoft / Okta.
 *   3. After the user signs in there, the API redirects back to
 *      /login/sso#code=...  (or #error=...). The code travels in the URL
 *      FRAGMENT, which browsers never send to any server.
 *   4. /login/sso calls `exchangeSsoCode(code)`, which returns the same
 *      access + refresh tokens a password login does, and saves them.
 */

import { API_BASE, setRefreshToken, setToken } from "./api";

const SSO_URL = `${API_BASE}/api/v1/auth/sso`;

export interface SsoConfig {
  enabled: boolean;
  provider_name: string;
}

const DISABLED: SsoConfig = { enabled: false, provider_name: "SSO" };

/**
 * Is SSO on? Any failure -- API down, an older backend without the route --
 * simply means "no button": a missing optional feature must never break the
 * login page.
 */
export async function getSsoConfig(): Promise<SsoConfig> {
  try {
    const response = await fetch(`${SSO_URL}/config`);
    if (!response.ok) return DISABLED;
    const data = (await response.json()) as Partial<SsoConfig>;
    return { enabled: Boolean(data.enabled), provider_name: data.provider_name || "SSO" };
  } catch {
    return DISABLED;
  }
}

/** Where the "Continue with SSO" link points. An invite token rides along. */
export function ssoLoginUrl(inviteToken?: string): string {
  return inviteToken
    ? `${SSO_URL}/login?invite=${encodeURIComponent(inviteToken)}`
    : `${SSO_URL}/login`;
}

/** Swap the one-time code for tokens, and save them like a normal login. */
export async function exchangeSsoCode(code: string): Promise<void> {
  const response = await fetch(`${SSO_URL}/exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!response.ok) {
    let detail = "This sign-in link has expired. Please sign in again.";
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      // Not JSON: keep the generic message.
    }
    throw new Error(detail);
  }
  const data = await response.json();
  setToken(data.access_token);
  if (data.refresh_token) setRefreshToken(data.refresh_token);
}

/** Read `#code=...` / `#error=...` from a URL fragment. */
export function parseSsoFragment(hash: string): { code?: string; error?: string } {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return {
    code: params.get("code") || undefined,
    error: params.get("error") || undefined,
  };
}
