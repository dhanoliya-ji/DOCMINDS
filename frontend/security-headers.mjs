/**
 * security-headers.mjs
 * ====================
 * The HTTP security headers every page of the frontend is served with. Used
 * by next.config.mjs; kept in its own module so a unit test
 * (tests/securityHeaders.test.ts) can check the policy without starting Next.
 *
 * WHAT EACH HEADER IS FOR
 * -----------------------
 *   Content-Security-Policy   which origins the page may load code, styles,
 *                             images and data from. The main defence-in-depth
 *                             against XSS: an injected <script src=evil> is
 *                             refused by the browser. The API origin
 *                             (NEXT_PUBLIC_API_URL) is the one external place
 *                             the page talks to, so it is the only one added
 *                             to connect-src.
 *   X-Frame-Options: DENY     no other site may put this app in an <iframe>
 *   (+ frame-ancestors)       (clickjacking). The CSP directive is the modern
 *                             form; the old header covers older browsers.
 *   Referrer-Policy           send only our origin, never full URLs, to other
 *                             sites -- URLs here can carry reset/invite tokens.
 *   Permissions-Policy        switch off powerful browser features this app
 *                             never uses, so injected code cannot use them.
 *   X-Content-Type-Options    stop browsers guessing a file's type ("sniffing")
 *                             and executing something served as text.
 *   Strict-Transport-Security production only: "always use HTTPS here".
 *
 * WHY 'unsafe-inline' FOR SCRIPTS
 * -------------------------------
 * Next.js writes small inline <script> tags to hydrate each page. The strict
 * alternative is a per-request nonce, which forces every page to render
 * dynamically (no static pages, no CDN caching) -- see Next's CSP guide. For
 * this app that trade is not worth it yet; the policy still blocks scripts
 * from any OTHER origin, plugins, framing and form posts elsewhere.
 * 'unsafe-eval' is added in development only, for React Refresh.
 */

/**
 * @param {{ apiUrl?: string, isDev?: boolean, analyticsHost?: string }} options
 * @returns {{ key: string, value: string }[]}
 */
export function securityHeaders({ apiUrl, isDev = false, analyticsHost = "" } = {}) {
  let apiOrigin = "";
  try {
    // An origin, not the full URL: CSP source expressions match origins, and
    // a trailing path would narrow it to that path only.
    apiOrigin = new URL(apiUrl || "http://localhost:8000").origin;
  } catch {
    apiOrigin = "";
  }

  // Optional PostHog (lib/analytics.ts): its host is allowed ONLY when
  // NEXT_PUBLIC_POSTHOG_KEY is set, so a deployment without analytics keeps
  // the tighter policy.
  let analyticsOrigin = "";
  if (analyticsHost) {
    try {
      analyticsOrigin = new URL(analyticsHost).origin;
    } catch {
      analyticsOrigin = "";
    }
  }

  const connect = ["'self'", apiOrigin, analyticsOrigin, isDev ? "ws: wss:" : ""]
    .filter(Boolean)
    .join(" ");

  const directives = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
    // Inline styles: framer-motion animates through the style attribute.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self' data:",
    `connect-src ${connect}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  // Only when the API is itself HTTPS: upgrading an http://localhost API call
  // to https would break local development and the end-to-end tests.
  if (!isDev && apiOrigin.startsWith("https://")) {
    directives.push("upgrade-insecure-requests");
  }

  const headers = [
    { key: "Content-Security-Policy", value: directives.join("; ") },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
    },
    { key: "X-Content-Type-Options", value: "nosniff" },
  ];
  if (!isDev) {
    headers.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
  }
  return headers;
}
