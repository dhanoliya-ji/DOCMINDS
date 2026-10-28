// The HTTP security headers every page of the frontend is served with.

/**
 * @param {{ apiUrl?: string, isDev?: boolean, analyticsHost?: string }} options
 * @returns {{ key: string, value: string }[]}
 */
export function securityHeaders({ apiUrl, isDev = false, analyticsHost = "" } = {}) {
  let apiOrigin = "";
  try {
    apiOrigin = new URL(apiUrl || "http://localhost:8000").origin;
  } catch {
    apiOrigin = "";
  }

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
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self' data:",
    `connect-src ${connect}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
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
