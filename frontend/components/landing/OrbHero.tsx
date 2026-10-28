// Lazy loader and poster for the 3D orb

"use client";

import dynamic from "next/dynamic";
import { useRef } from "react";

import { useIsActive, usePrefersReducedMotion } from "./hooks";

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
