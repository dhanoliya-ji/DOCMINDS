// A mock document page is shown blurred.

"use client";

import { type PointerEvent, useRef, useState } from "react";

import { useCanHover, usePrefersReducedMotion } from "./hooks";

const PARAGRAPHS = [
  "4.1 Scope. This policy applies to all permanent employees and fixed-term staff engaged for more than six months.",
  "4.2 Annual Leave Entitlement. Full-time employees accrue 25 days of paid annual leave per holiday year, pro-rated for part-time staff.",
  "4.3 Carry-over. Up to five unused days may be carried into the following year with written approval from a line manager.",
  "4.4 Public holidays. Bank and public holidays are granted in addition to the annual entitlement above.",
];
const CITED = 1;

function DocPage({ highlight }: { highlight: boolean }) {
  return (
    <div className="space-y-4 p-6 text-left text-sm leading-relaxed sm:p-8">
      <p className="font-mono text-xs" style={{ color: "var(--text-muted)" }}>
        employee-handbook.pdf · page 12
      </p>
      {PARAGRAPHS.map((text, i) => (
        <p
          key={text}
          className="rounded-md px-2 py-1"
          style={
            highlight && i === CITED
              ? {
                  background: "rgba(251,191,36,0.16)",
                  boxShadow: "inset 3px 0 0 var(--warning)",
                  color: "var(--text-primary)",
                }
              : { color: "var(--text-secondary)" }
          }
        >
          {text}
        </p>
      ))}
    </div>
  );
}

export default function Flashlight() {
  const reduced = usePrefersReducedMotion();
  const canHover = useCanHover();
  const ref = useRef<HTMLDivElement>(null);
  const [focused, setFocused] = useState(false);
  const interactive = canHover && !reduced && !focused;

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };

  return (
    <figure className="mx-auto w-full max-w-2xl">
      <div
        ref={ref}
        tabIndex={0}
        role="group"
        aria-label="Example source document with the cited passage on annual leave highlighted"
        onPointerMove={interactive ? onMove : undefined}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        data-testid="flashlight"
        data-mode={interactive ? "interactive" : "static"}
        className="glass relative overflow-hidden focus-visible:outline-2 focus-visible:outline-offset-4"
        style={{ outlineColor: "var(--accent-hover)" }}
      >
        {interactive ? (
          <>
            <div aria-hidden="true" className="select-none blur-[5px]">
              <DocPage highlight={false} />
            </div>
            <div
              className="absolute inset-0"
              style={{
                WebkitMaskImage:
                  "radial-gradient(circle 130px at var(--mx, 50%) var(--my, 45%), #000 55%, transparent 100%)",
                maskImage:
                  "radial-gradient(circle 130px at var(--mx, 50%) var(--my, 45%), #000 55%, transparent 100%)",
              }}
            >
              <DocPage highlight />
            </div>
          </>
        ) : (
          <DocPage highlight />
        )}
      </div>
      <figcaption className="mt-3 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
        “How much annual leave do I get?” → <strong>25 days</strong>{" "}
        <span className="font-mono" style={{ color: "var(--accent-hover)" }}>
          [employee-handbook.pdf, p.12]
        </span>
      </figcaption>
    </figure>
  );
}
