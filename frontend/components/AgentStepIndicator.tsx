/**
 * AgentStepIndicator.tsx
 * ======================
 * The live "what is the agent doing right now" badge shown while a Deep
 * research answer streams (F10).
 *
 * WHAT IT SHOWS
 * -------------
 * The agent works in three broad phases, read off the stream as it arrives:
 *
 *     search      no step yet, or the latest tool was search_documents
 *     read        the latest tool was list_documents / read_document_section
 *     summarise   the answer's first words have arrived
 *
 * One SVG glyph MORPHS between a lens (search), a page (read) and a spark
 * (summarise), next to a sentence saying the same thing, and three small
 * dots mark how far along the three phases it is.
 *
 * HOW THE MORPH WORKS
 * -------------------
 * framer-motion can animate an SVG path's `d` attribute, but only between
 * paths with the same SHAPE of commands -- it interpolates the numbers. So
 * all three glyphs are drawn the same way: a start point and four cubic
 * Bézier curves, through the top, right, bottom and left. A circle, a
 * rounded rectangle and a four-pointed star are just different control
 * points on that one skeleton, and the motion between them is smooth.
 * No extra library: a few hundred bytes of path data.
 *
 * REDUCED MOTION
 * --------------
 * Under prefers-reduced-motion the glyph swaps instantly and nothing spins
 * or pulses -- the words and the dots carry the same information.
 *
 * ACCESSIBILITY
 * -------------
 * role="status" (an implicit aria-live="polite"): a screen reader hears
 * "Searching your documents", then "Reading the relevant sections", without
 * interrupting. The glyph is decorative and hidden from it.
 */

"use client";

import { motion } from "framer-motion";

import { usePrefersReducedMotion } from "@/components/landing/hooks";
import type { AgentStep } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type AgentPhase = "search" | "read" | "summarise";

export const AGENT_PHASES: AgentPhase[] = ["search", "read", "summarise"];

/** Tools that READ rather than search, by the names the backend reports. */
const READ_TOOLS = new Set(["list_documents", "read_document_section"]);

/**
 * Which phase a streaming answer is in. Exported for the tests.
 *
 * Text beats steps: once words are arriving the agent has stopped using
 * tools and is writing.
 */
export function agentPhase(steps: AgentStep[] | undefined, content: string): AgentPhase {
  if (content) return "summarise";
  const last = steps?.[steps.length - 1];
  if (last && READ_TOOLS.has(last.tool)) return "read";
  return "search";
}

/** The three glyphs: same skeleton (M + four C + Z), different numbers. */
const GLYPHS: Record<AgentPhase, string> = {
  // A circle of radius 7 (0.5523 x r is the standard Bézier circle handle).
  search:
    "M12 5 C15.87 5 19 8.13 19 12 C19 15.87 15.87 19 12 19 C8.13 19 5 15.87 5 12 C5 8.13 8.13 5 12 5 Z",
  // A rounded page: the handles sit on the corners.
  read: "M12 4 C18 4 18 4 18 12 C18 20 18 20 12 20 C6 20 6 20 6 12 C6 4 6 4 12 4 Z",
  // A four-pointed spark: the handles pull in towards the centre.
  summarise:
    "M12 3 C12.8 9.5 14.5 11.2 21 12 C14.5 12.8 12.8 14.5 12 21 C11.2 14.5 9.5 12.8 3 12 C9.5 11.2 11.2 9.5 12 3 Z",
};

const LABELS: Record<AgentPhase, string> = {
  search: "chat.phase.search",
  read: "chat.phase.read",
  summarise: "chat.phase.summarise",
};

export default function AgentStepIndicator({ phase }: { phase: AgentPhase }) {
  const reduced = usePrefersReducedMotion();
  const { t } = useT();
  const index = AGENT_PHASES.indexOf(phase);

  return (
    <div
      role="status"
      data-testid="agent-step-indicator"
      data-phase={phase}
      data-reduced-motion={reduced ? "true" : "false"}
      className="mb-2 inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs"
      style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <motion.path
          d={GLYPHS[phase]}
          initial={false}
          animate={reduced ? { d: GLYPHS[phase] } : { d: GLYPHS[phase], rotate: phase === "search" ? 360 : 0 }}
          transition={
            reduced
              ? { duration: 0 }
              : {
                  d: { duration: 0.45, ease: [0.4, 0, 0.2, 1] },
                  // The lens slowly circles while searching.
                  rotate: phase === "search" ? { duration: 3, repeat: Infinity, ease: "linear" } : { duration: 0.3 },
                }
          }
          style={{ transformOrigin: "12px 12px" }}
          fill="none"
          stroke="var(--accent-hover)"
          strokeWidth="2"
          strokeLinejoin="round"
        />
      </svg>
      <span>{t(LABELS[phase])}</span>
      {/* Progress through the three phases. */}
      <span className="flex items-center gap-1" aria-hidden="true">
        {AGENT_PHASES.map((p, i) => (
          <motion.span
            key={p}
            className="h-1.5 w-1.5 rounded-full"
            initial={false}
            animate={{ scale: i === index && !reduced ? [1, 1.4, 1] : 1 }}
            transition={i === index && !reduced ? { duration: 1.2, repeat: Infinity } : { duration: 0 }}
            style={{ background: i <= index ? "var(--accent-hover)" : "var(--border-subtle)" }}
          />
        ))}
      </span>
    </div>
  );
}
