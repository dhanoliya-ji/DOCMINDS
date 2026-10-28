// Next.js configuration.

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
          analyticsHost: process.env.NEXT_PUBLIC_POSTHOG_KEY
            ? process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com"
            : "",
        }),
      },
    ];
  },
};

export default nextConfig;
