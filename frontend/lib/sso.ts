// The three calls behind "Continue with SSO" (backend/app/api/v1/sso.py).

import { API_BASE, setRefreshToken, setToken } from "./api";

const SSO_URL = `${API_BASE}/api/v1/auth/sso`;

export interface SsoConfig {
  enabled: boolean;
  provider_name: string;
}

const DISABLED: SsoConfig = { enabled: false, provider_name: "SSO" };

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

export function ssoLoginUrl(inviteToken?: string): string {
  return inviteToken
    ? `${SSO_URL}/login?invite=${encodeURIComponent(inviteToken)}`
    : `${SSO_URL}/login`;
}

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

export function parseSsoFragment(hash: string): { code?: string; error?: string } {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return {
    code: params.get("code") || undefined,
    error: params.get("error") || undefined,
  };
}
