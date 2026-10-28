// Cards that stack on scroll

"use client";

import { type MotionValue, motion, useScroll, useTransform } from "framer-motion";
import { useRef } from "react";

import { usePrefersReducedMotion } from "./hooks";

export interface StackItem {
  title: string;
  body: string;
  tag: string;
}

function StackCard({
  item,
  index,
  total,
  progress,
  still,
}: {
  item: StackItem;
  index: number;
  total: number;
  progress: MotionValue<number>;
  still: boolean;
}) {
  const start = index / total;
  const scale = useTransform(progress, [start, 1], [1, 1 - (total - index) * 0.04]);
  const brightness = useTransform(progress, [start, 1], [1, 0.7]);
  const filter = useTransform(brightness, (b) => `brightness(${b})`);

  return (
    <div
      className={still ? "" : "sticky"}
      style={still ? undefined : { top: `${96 + index * 24}px` }}
    >
      <motion.article
        className="glass mb-6 origin-top p-6 sm:p-8"
        style={
          still
            ? undefined
            : { scale, filter, background: "var(--bg-elevated)" }
        }
      >
        <span className="pill-info">{item.tag}</span>
        <h3 className="mt-3 text-xl font-semibold">{item.title}</h3>
        <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {item.body}
        </p>
      </motion.article>
    </div>
  );
}

export default function StackingCards({ items }: { items: StackItem[] }) {
  const reduced = usePrefersReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });

  return (
    <div
      ref={ref}
      data-testid="stacking-cards"
      data-mode={reduced ? "static" : "stacking"}
      className="relative mx-auto max-w-2xl"
    >
      {items.map((item, i) => (
        <StackCard
          key={item.title}
          item={item}
          index={i}
          total={items.length}
          progress={scrollYProgress}
          still={reduced}
        />
      ))}
    </div>
  );
}
