/**
 * share/layout.tsx
 * ================
 * Server-side metadata for the public share pages, which are client
 * components and cannot export it themselves:
 *
 *   * robots noindex/nofollow -- a shared link is for the people it was sent
 *     to, not for search engines;
 *   * referrer no-referrer -- a link clicked on a shared page must not hand
 *     the page's URL (which IS the token) to the next site.
 */

import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Shared from DocMinds",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

export default function ShareLayout({ children }: { children: ReactNode }) {
  return children;
}
