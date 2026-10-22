/**
 * next.config.mjs
 * ===============
 * Next.js configuration. Its one job today: attach the security headers from
 * security-headers.mjs (CSP, X-Frame-Options, Referrer-Policy,
 * Permissions-Policy, ...) to every route. See that file for what each header
 * does and why the policy is shaped as it is.
 *
 * NEXT_PUBLIC_API_URL is read at BUILD time, like everywhere else in the
 * frontend: changing the API's address means rebuilding, or the CSP would
 * block requests to the new origin.
 */

import { securityHeaders } from "./security-headers.mjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders({
          apiUrl: process.env.NEXT_PUBLIC_API_URL,
          isDev: process.env.NODE_ENV === "development",
          // Only with a PostHog key is its host added to the CSP.
          analyticsHost: process.env.NEXT_PUBLIC_POSTHOG_KEY
            ? process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com"
            : "",
        }),
      },
    ];
  },
};

export default nextConfig;
