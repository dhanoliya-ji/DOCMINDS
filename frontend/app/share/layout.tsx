// Server-side metadata for the public share pages, which are client components and...

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
