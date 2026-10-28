// The ROOT LAYOUT.

import type { Metadata } from "next";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import "./globals.css";

import AnalyticsConsent from "@/components/AnalyticsConsent";
import PwaSupport from "@/components/PwaSupport";

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
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="antialiased">
        {children}
        <AnalyticsConsent />
        <PwaSupport />
      </body>
    </html>
  );
}
