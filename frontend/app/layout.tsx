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
import "./globals.css";

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
    <html lang="en">
      {/* `antialiased` smooths font rendering, which noticeably improves the
          look of light text on a dark background. */}
      <body className="antialiased">{children}</body>
    </html>
  );
}
