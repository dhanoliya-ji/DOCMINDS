/**
 * PipelineScroll.tsx  (F3 + F8: scroll-driven "how it works")
 * ===========================================================
 * A tall section whose inner frame is pinned (`position: sticky`) while the
 * visitor scrolls through it. Scroll progress drives a camera "dolly" through
 * four stages laid out in depth -- upload → chunk → embed → answer -- and the
 * callout for each stage is revealed in sequence (F8).
 *
 * WHY framer-motion useScroll, NOT R3F ScrollControls
 * ---------------------------------------------------
 * ScrollControls owns its own scroll container, which fights Lenis and
 * hijacks native scrolling for the whole page. useScroll reads the real
 * document scroll, so the effect costs no WebGL at all: the "depth" is CSS
 * 3D transforms on ordinary, accessible HTML.
 *
 * REDUCED MOTION
 * --------------
 * The pinned theatre is replaced by a plain ordered list of the same stages.
 */

"use client";

import {
  type MotionValue,
  motion,
  useScroll,
  useTransform,
} from "framer-motion";
import { Cpu, FileUp, MessageSquareQuote, Scissors } from "lucide-react";
import { useRef } from "react";

import { usePrefersReducedMotion } from "./hooks";

export const STAGES = [
  {
    icon: FileUp,
    name: "Upload",
    detail:
      "Drop in PDFs, decks, spreadsheets or scans. Each file is routed to the right parser by extension; low-text PDF pages go through OCR.",
  },
  {
    icon: Scissors,
    name: "Chunk",
    detail:
      "Text is split into ~400-token pieces page by page, with overlap so no sentence is lost at a boundary.",
  },
  {
    icon: Cpu,
    name: "Embed",
    detail:
      "Each chunk becomes a 384-dimension vector describing its meaning, stored in pgvector behind an HNSW index.",
  },
  {
    icon: MessageSquareQuote,
    name: "Answer",
    detail:
      "Your question is embedded too, the closest chunks are retrieved, and the model answers only from them — citing each one.",
  },
];

/** Distance between stages along the virtual Z axis, in px. */
const GAP = 600;

function Stage({
  index,
  progress,
}: {
  index: number;
  progress: MotionValue<number>;
}) {
  const stage = STAGES[index];
  const Icon = stage.icon;
  const n = STAGES.length;
  // Where this stage sits in 0..1 scroll progress.
  const centre = index / (n - 1);
  const span = 1 / (n - 1);

  // The camera moves forward through Z: each stage starts far away, arrives
  // at the screen plane when the scroll reaches it, then passes the viewer.
  const z = useTransform(progress, [centre - span, centre, centre + span], [-GAP, 0, GAP * 0.6]);
  const opacity = useTransform(
    progress,
    [centre - span, centre - span * 0.35, centre + span * 0.35, centre + span * 0.8],
    [0, 1, 1, 0]
  );
  // The callout trails its stage slightly, so reveals read in sequence.
  const calloutOpacity = useTransform(progress, [centre - span * 0.2, centre], [0, 1]);
  const calloutY = useTransform(progress, [centre - span * 0.2, centre], [16, 0]);

  return (
    <motion.div
      className="absolute inset-0 flex items-center justify-center px-4"
      style={{ z, opacity }}
    >
      <div className="glass w-full max-w-md p-6 text-center sm:p-8">
        <div
          className="mx-auto mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl"
          style={{ background: "rgba(99,102,241,0.16)", color: "var(--accent-hover)" }}
        >
          <Icon size={26} aria-hidden="true" />
        </div>
        <p className="font-mono text-xs" style={{ color: "var(--accent-hover)" }}>
          0{index + 1} / 0{n}
        </p>
        <h3 className="mt-1 text-2xl font-bold">{stage.name}</h3>
        <motion.p
          className="mt-3 text-sm leading-relaxed"
          style={{ color: "var(--text-secondary)", opacity: calloutOpacity, y: calloutY }}
        >
          {stage.detail}
        </motion.p>
      </div>
    </motion.div>
  );
}

function StaticPipeline() {
  return (
    <ol className="mx-auto grid max-w-5xl gap-4 px-4 sm:grid-cols-2 sm:px-6">
      {STAGES.map((stage, i) => {
        const Icon = stage.icon;
        return (
          <li key={stage.name} className="glass p-6">
            <Icon size={22} aria-hidden="true" style={{ color: "var(--accent-hover)" }} />
            <h3 className="mt-3 font-semibold">
              <span className="font-mono text-sm" style={{ color: "var(--accent-hover)" }}>
                0{i + 1}{" "}
              </span>
              {stage.name}
            </h3>
            <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
              {stage.detail}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function AnimatedPipeline() {
  const ref = useRef<HTMLDivElement>(null);
  // 0 when the section's top hits the viewport top, 1 when its bottom does.
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });
  const bar = useTransform(scrollYProgress, [0, 1], ["0%", "100%"]);

  return (
    // 400vh of scroll for four stages; the inner frame is pinned throughout.
    <div ref={ref} className="relative" style={{ height: `${STAGES.length * 100}vh` }}>
      <div
        className="sticky top-0 h-screen overflow-hidden"
        style={{ perspective: "900px" }}
      >
        <div className="relative h-full" style={{ transformStyle: "preserve-3d" }}>
          {STAGES.map((stage, i) => (
            <Stage key={stage.name} index={i} progress={scrollYProgress} />
          ))}
        </div>
        {/* Progress rail */}
        <div
          aria-hidden="true"
          className="absolute bottom-10 left-1/2 h-1 w-40 -translate-x-1/2 overflow-hidden rounded-full"
          style={{ background: "var(--border-strong)" }}
        >
          <motion.div className="h-full" style={{ width: bar, background: "var(--accent)" }} />
        </div>
      </div>
    </div>
  );
}

export default function PipelineScroll() {
  const reduced = usePrefersReducedMotion();
  return (
    <section aria-labelledby="how-heading" className="py-16">
      <h2 id="how-heading" className="mb-6 px-4 text-center text-3xl font-bold md:text-4xl">
        How a question gets answered
      </h2>
      {reduced ? (
        <div data-testid="pipeline-static">
          <StaticPipeline />
        </div>
      ) : (
        <div data-testid="pipeline-animated">
          <AnimatedPipeline />
        </div>
      )}
    </section>
  );
}
