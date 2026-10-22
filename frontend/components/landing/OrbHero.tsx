/**
 * OrbHero.tsx  (F2: lazy loader and poster for the 3D orb)
 * ========================================================
 * Decides whether the 3D orb is worth loading, and shows a static CSS
 * "poster" orb until it is (or instead of it).
 *
 * WHY next/dynamic WITH ssr:false
 * -------------------------------
 * three.js touches `window` and WebGL at import time, so it cannot run on the
 * server. `ssr: false` keeps it out of the server bundle; `loading` renders
 * the poster while the chunk downloads, so there is never an empty hole.
 *
 * Under prefers-reduced-motion the dynamic import is never even triggered.
 */

"use client";

import dynamic from "next/dynamic";
import { useRef } from "react";

import { useIsActive, usePrefersReducedMotion } from "./hooks";

/** The static fallback: a layered radial gradient that resembles the orb. */
function OrbPoster() {
  return (
    <div
      data-testid="orb-poster"
      aria-hidden="true"
      className="absolute inset-0 m-auto aspect-square w-3/5 rounded-full"
      style={{
        background:
          "radial-gradient(circle at 35% 30%, #f0abfc 0%, transparent 35%), radial-gradient(circle at 70% 70%, var(--cyan) 0%, transparent 45%), radial-gradient(circle at 50% 50%, var(--accent) 0%, var(--violet) 60%, transparent 72%)",
        filter: "blur(2px)",
        boxShadow: "0 0 120px var(--accent-glow)",
      }}
    />
  );
}

const OrbScene = dynamic(() => import("./OrbScene"), {
  ssr: false,
  loading: OrbPoster,
});

export default function OrbHero() {
  const reduced = usePrefersReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const active = useIsActive(ref);

  return (
    <div
      ref={ref}
      className="relative mx-auto aspect-square w-full max-w-[420px]"
    >
      {reduced ? <OrbPoster /> : <OrbScene active={active} />}
    </div>
  );
}
