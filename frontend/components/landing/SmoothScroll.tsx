/**
 * SmoothScroll.tsx  (F7: Lenis smooth scrolling, landing page only)
 * =================================================================
 * Lenis interpolates the native scroll position for an inertial feel. It is
 * mounted by the landing page alone, so the dashboard and project pages keep
 * the browser's plain scrolling -- smoothing there would make dense UIs feel
 * laggy.
 *
 * Lenis drives the real document scroll (it does not fake it with
 * transforms), so framer-motion's useScroll, sticky positioning and anchor
 * links keep working. It is skipped entirely under reduced motion, and the
 * instance is destroyed on unmount so navigating away restores native scroll.
 */

"use client";

import Lenis from "lenis";
import { useEffect } from "react";

import { usePrefersReducedMotion } from "./hooks";

export default function SmoothScroll() {
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    if (reduced) return;
    const lenis = new Lenis({ duration: 1.1, smoothWheel: true });
    let frame = 0;
    const raf = (time: number) => {
      lenis.raf(time);
      frame = requestAnimationFrame(raf);
    };
    frame = requestAnimationFrame(raf);
    return () => {
      cancelAnimationFrame(frame);
      lenis.destroy();
    };
  }, [reduced]);

  return null;
}
