// The capability grid.

"use client";

import {
  type MotionValue,
  motion,
  useMotionValue,
  useScroll,
  useSpring,
  useTransform,
} from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { type PointerEvent, useRef } from "react";

import { usePrefersReducedMotion } from "./hooks";

export interface Feature {
  icon: LucideIcon;
  title: string;
  body: string;
}

function Card({
  feature,
  index,
  progress,
  still,
}: {
  feature: Feature;
  index: number;
  progress: MotionValue<number>;
  still: boolean;
}) {
  const Icon = feature.icon;
  const depth = 30 + (index % 3) * 25;
  const bodyY = useTransform(progress, [0, 1], [depth, -depth]);
  const glowY = useTransform(progress, [0, 1], [depth * 1.8, -depth * 1.8]);
  const iconY = useTransform(progress, [0, 1], [depth * 0.4, -depth * 0.4]);

  const rx = useSpring(useMotionValue(0), { stiffness: 200, damping: 20 });
  const ry = useSpring(useMotionValue(0), { stiffness: 200, damping: 20 });
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    ry.set(((e.clientX - r.left) / r.width - 0.5) * 10);
    rx.set(-((e.clientY - r.top) / r.height - 0.5) * 10);
  };
  const onLeave = () => {
    rx.set(0);
    ry.set(0);
  };

  return (
    <motion.div
      className="relative"
      style={still ? undefined : { y: bodyY, perspective: 800 }}
    >
      {!still && (
        <motion.div
          aria-hidden="true"
          className="absolute -inset-2 -z-10 rounded-3xl blur-2xl"
          style={{ y: glowY, background: "var(--accent-glow)", opacity: 0.35 }}
        />
      )}
      <motion.article
        onPointerMove={still ? undefined : onMove}
        onPointerLeave={still ? undefined : onLeave}
        className="glass glass-hover h-full p-6"
        style={still ? undefined : { rotateX: rx, rotateY: ry }}
      >
        <motion.div
          className="mb-4 inline-flex h-10 w-10 items-center justify-center rounded-xl"
          style={{
            background: "rgba(99,102,241,0.14)",
            color: "var(--accent-hover)",
            ...(still ? {} : { y: iconY }),
          }}
        >
          <Icon size={19} aria-hidden="true" />
        </motion.div>
        <h3 className="mb-2 font-semibold">{feature.title}</h3>
        <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {feature.body}
        </p>
      </motion.article>
    </motion.div>
  );
}

export default function ParallaxCards({ features }: { features: Feature[] }) {
  const reduced = usePrefersReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end start"] });

  return (
    <div
      ref={ref}
      data-testid="parallax-cards"
      data-mode={reduced ? "static" : "parallax"}
      className="grid gap-6 md:grid-cols-2 lg:grid-cols-3"
    >
      {features.map((f, i) => (
        <Card key={f.title} feature={f} index={i} progress={scrollYProgress} still={reduced} />
      ))}
    </div>
  );
}
