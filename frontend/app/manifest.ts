/**
 * manifest.ts -- served by Next.js as /manifest.webmanifest and linked from
 * every page's <head>. It is what makes DocMinds installable: its name, the
 * icon, and that it opens standalone at the dashboard. The service worker
 * (public/sw.js) supplies the offline page installability also expects.
 */

import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "DocMinds — Document Intelligence",
    short_name: "DocMinds",
    description: "Ask your documents anything, with page-level citations.",
    start_url: "/dashboard",
    scope: "/",
    display: "standalone",
    background_color: "#070b18",
    theme_color: "#070b18",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
  };
}
