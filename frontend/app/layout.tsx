/**
 * layout.tsx
 * ==========
 * The ROOT LAYOUT. In the Next.js App Router this is the outermost shell that
 * wraps every single page, and it is the only place allowed to render the
 * <html> and <body> tags.
 *
 * Anything placed here appears on every page and, importantly, does NOT
 * re-render when the user navigates between pages. That makes it the right
 * home for site-wide chrome and providers, and the wrong home for
 * page-specific content.
 */

import type { Metadata } from "next";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import "./globals.css";

import AnalyticsConsent from "@/components/AnalyticsConsent";
import PwaSupport from "@/components/PwaSupport";

/**
 * `metadata` is picked up by Next.js and turned into <head> tags at build
 * time. This drives the browser tab title, search engine snippets, and the
 * preview card shown when the link is shared on Slack, LinkedIn or X.
 */
export const metadata: Metadata = {
  title: "DocMinds — Document Intelligence",
  description:
    "Upload documents in 19 formats and ask questions answered from their contents, with page-level citations. Built with FastAPI, pgvector and GPT-OSS on Groq.",
  keywords: [
    "RAG",
    "semantic search",
    "pgvector",
    "document intelligence",
    "FastAPI",
  ],
  openGraph: {
    title: "DocMinds — Document Intelligence",
    description:
      "Retrieval-augmented question answering over your own documents, with citations.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  // `children` is whichever page is currently being displayed. Next.js passes
  // it in automatically; the layout simply decides where to place it.
  children: React.ReactNode;
}) {
  return (
    // `lang="en"` matters for screen readers, which use it to select the right
    // pronunciation rules.
    //
    // `suppressHydrationWarning` because the inline script below sets
    // `data-theme` on <html> before React hydrates, so the server's markup and
    // the live DOM legitimately differ on that one attribute.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* No-flash theming: runs before first paint and picks light or dark
            from the stored choice or the OS preference. See lib/theme.ts. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      {/* `antialiased` smooths font rendering, which noticeably improves the
          look of light text on a dark background. */}
      <body className="antialiased">
        {children}
        {/* Optional PostHog analytics: renders nothing unless
            NEXT_PUBLIC_POSTHOG_KEY is set, and sends nothing until the
            visitor accepts (lib/analytics.ts). */}
        <AnalyticsConsent />
        {/* Service worker (production only) and the "Install app" offer. */}
        <PwaSupport />
      </body>
    </html>
  );
}
