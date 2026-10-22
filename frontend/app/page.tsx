/**
 * page.tsx  (the landing page, served at "/")
 * ===========================================
 * The first thing a visitor sees. It explains what the product does and sends
 * them to sign in.
 *
 * WHY "use client"?
 * -----------------
 * Next.js renders components on the SERVER by default, which is faster and
 * better for search engines. But server components cannot use browser-only
 * features -- no hooks, no event handlers, no animation.
 *
 * This page uses Framer Motion and reads localStorage, so it must run in the
 * browser. `"use client"` at the very top of the file is how you say so.
 *
 * HOW THE PAGE IS BUILT
 * ---------------------
 * The heavy visual effects live in components/landing/, one file per effect,
 * each with its own prefers-reduced-motion fallback:
 *
 *     MeshGradient      WebGL colour field behind the hero        (F1)
 *     OrbHero           lazy-loaded 3D orb, static poster first   (F2)
 *     ParticleHeadline  canvas headline over a real <h1>          (F5)
 *     ParallaxCards     depth-layered feature grid                (F6)
 *     SmoothScroll      Lenis, mounted on this page only          (F7)
 *     PipelineScroll    pinned, scroll-driven "how it works"      (F3, F8)
 *     Flashlight        cursor-revealed cited passage             (F4)
 *     StackingCards     sticky cards that pile up on scroll       (F16)
 *
 * Everything 3D is loaded with next/dynamic (ssr:false), DPR is capped and
 * every animation loop pauses when off screen or in a hidden tab (F19).
 */

"use client";

import { motion } from "framer-motion";
import {
  ArrowRight,
  FileSearch,
  Layers,
  Quote,
  ScanText,
  Sparkles,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useSyncExternalStore } from "react";

import Flashlight from "@/components/landing/Flashlight";
import { usePrefersReducedMotion } from "@/components/landing/hooks";
import MeshGradient from "@/components/landing/MeshGradient";
import OrbHero from "@/components/landing/OrbHero";
import ParallaxCards from "@/components/landing/ParallaxCards";
import ParticleHeadline from "@/components/landing/ParticleHeadline";
import PipelineScroll from "@/components/landing/PipelineScroll";
import SmoothScroll from "@/components/landing/SmoothScroll";
import StackingCards, { type StackItem } from "@/components/landing/StackingCards";
import { getToken } from "@/lib/api";

/**
 * ANIMATION PRESETS
 * -----------------
 * Framer Motion animates between named states. `hidden` is where an element
 * starts, `visible` is where it ends up. Defining them once here keeps the
 * motion consistent across the page.
 */
const fadeUp = {
  // Start slightly lower and fully transparent...
  hidden: { opacity: 0, y: 24 },
  // ...then rise into place and fade in.
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, ease: [0.22, 1, 0.36, 1] as const },
  },
};

/**
 * A container that staggers its children.
 * `staggerChildren` delays each child by a fixed amount, so a grid of cards
 * animates in one after another rather than all at once. That reads as
 * deliberate and polished, where a simultaneous fade reads as flat.
 */
const stagger = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.09, delayChildren: 0.15 },
  },
};

/** The capability cards. Kept as data so the JSX below stays a simple loop. */
const FEATURES = [
  {
    icon: Layers,
    title: "19 file extensions",
    body: "PDF, DOCX, PPTX, XLSX, HTML, EML, CSV, images and ZIP archives — each parsed by a library chosen for that format, with ZIPs unpacked recursively.",
  },
  {
    icon: ScanText,
    title: "Automatic OCR",
    body: "A PDF page yielding under 100 characters is treated as a scan: it is rasterised at 150 DPI and passed through Tesseract, so image-only documents become searchable too.",
  },
  {
    icon: FileSearch,
    title: "Search by meaning",
    body: "Ask about “time off” and find a section headed “Annual Leave Entitlement”. Text is embedded into 384-dimension vectors and compared by cosine distance in pgvector.",
  },
  {
    icon: Quote,
    title: "Every answer cited",
    body: "The model may only answer from retrieved excerpts, and each claim carries a bracketed citation you can expand to see the exact file and page it came from.",
  },
  {
    icon: Zap,
    title: "Processed in the background",
    body: "Uploads return instantly. Celery workers handle extraction, chunking and embedding, so a 200-page scan never blocks the browser.",
  },
  {
    icon: Sparkles,
    title: "Multi-tenant by design",
    body: "Every search joins up the ownership chain and filters on your organization, so one tenant's documents can never surface in another's results.",
  },
];

/** The "why trust it" deck that stacks on scroll (F16). */
const GUARANTEES: StackItem[] = [
  {
    tag: "Grounded",
    title: "No answer without a source",
    body: "If the retrieved excerpts do not contain the answer, the model says so instead of guessing. Outside knowledge is forbidden by the prompt.",
  },
  {
    tag: "Traceable",
    title: "Every claim links to a page",
    body: "Citations name the file and page, and expand inline to show the exact excerpt that was used.",
  },
  {
    tag: "Isolated",
    title: "Your documents stay yours",
    body: "Every query is scoped to your organization at the database level, so results can never cross tenants.",
  },
];

/** The headline, split into the lines both the <h1> and the canvas render. */
const HEADLINE = ["Ask your documents", "anything."];

/** Numbers shown in the hero strip. */
const STATS = [
  { value: "19", label: "file extensions" },
  { value: "384", label: "vector dimensions" },
  { value: "3", label: "chunking strategies" },
  { value: "<50ms", label: "typical search" },
];

/**
 * Tell React when the stored token might have changed.
 *
 * The `storage` event fires when ANOTHER tab writes to localStorage, so
 * signing out in one tab updates this page in the others. It does not fire for
 * changes made by this tab -- which is fine here, because signing in navigates
 * away from this page anyway.
 *
 * The returned function unsubscribes, which React calls on unmount.
 */
function subscribeToTokenChanges(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

export default function LandingPage() {
  // Whether the visitor already has a saved login token, so the call to action
  // can read "Open dashboard" instead of "Get started".
  //
  // WHY useSyncExternalStore AND NOT useState + useEffect
  // -----------------------------------------------------
  // localStorage is state that lives OUTSIDE React, and this is the hook React
  // provides for reading exactly that. Its third argument is the value to use
  // during server rendering: there is no localStorage on the server, so the
  // server renders `false` and the browser corrects it on hydration.
  const signedIn = useSyncExternalStore(
    subscribeToTokenChanges,
    () => Boolean(getToken()),
    () => false
  );

  // With reduced motion, entrance animations start already "visible".
  const reduced = usePrefersReducedMotion();
  const initial = reduced ? "visible" : "hidden";

  return (
    // `overflow-x-clip`, not `overflow-hidden`: hidden would make <main> a
    // scroll container and silently break every `position: sticky` below.
    <main className="relative min-h-screen overflow-x-clip">
      <SmoothScroll />

      {/* ============================================================
          HERO
          ============================================================ */}
      <section
        aria-label="Introduction"
        className="relative isolate mx-auto grid max-w-6xl items-center gap-10 px-4 pt-20 pb-16 sm:px-6 md:grid-cols-[1.2fr_1fr] md:pt-28"
      >
        <MeshGradient />

        <motion.div
          initial={initial}
          animate="visible"
          variants={stagger}
          className="min-w-0 text-center md:text-left"
        >
          <motion.div variants={fadeUp} className="mb-6 flex justify-center md:justify-start">
            <span className="pill-info gap-2">
              <Sparkles size={13} aria-hidden="true" />
              Retrieval-Augmented Generation over your own documents
            </span>
          </motion.div>

          <motion.div variants={fadeUp}>
            <ParticleHeadline
              lines={HEADLINE}
              className="text-4xl font-bold leading-[1.1] tracking-tight sm:text-5xl md:text-6xl"
            />
          </motion.div>

          <motion.p
            variants={fadeUp}
            className="mx-auto mt-6 max-w-xl text-lg leading-relaxed md:mx-0"
            style={{ color: "var(--text-secondary)" }}
          >
            Upload contracts, handbooks, decks and scanned reports. Every page is
            read, split into meaning-aware chunks and indexed as vectors — so you
            can ask a question in plain English and get an answer that cites the
            exact page it came from.
          </motion.p>

          {/* Call to action */}
          <motion.div
            variants={fadeUp}
            className="mt-10 flex flex-wrap items-center justify-center gap-3 md:justify-start"
          >
            <Link
              href={signedIn ? "/dashboard" : "/login"}
              className="btn-primary px-6 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              {signedIn ? "Open dashboard" : "Get started free"}
              <ArrowRight size={17} aria-hidden="true" />
            </Link>
            <a
              href="https://github.com/dhanoliya-ji/DocMinds"
              target="_blank"
              // `noopener noreferrer` on any target="_blank" link: without it,
              // the opened page gets a handle back to this one and can
              // redirect it. This is a standard security precaution.
              rel="noopener noreferrer"
              className="btn-ghost px-6 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              View source
            </a>
          </motion.div>
        </motion.div>

        <OrbHero />

        {/* Stat strip, full width under both columns */}
        <ul className="grid grid-cols-2 gap-4 md:col-span-2 md:grid-cols-4">
          {STATS.map((stat) => (
            <li key={stat.label} className="glass px-4 py-5 text-center">
              <div className="text-2xl font-bold" style={{ color: "var(--accent-hover)" }}>
                {stat.value}
              </div>
              <div className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                {stat.label}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* ============================================================
          FEATURE GRID (F6)
          ============================================================ */}
      <section aria-labelledby="features-heading" className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <h2 id="features-heading" className="mb-10 text-center text-3xl font-bold md:text-4xl">
          Built for real document piles
        </h2>
        <ParallaxCards features={FEATURES} />
      </section>

      {/* ============================================================
          HOW IT WORKS (F3, F8) -- pinned while it plays
          ============================================================ */}
      <PipelineScroll />

      {/* ============================================================
          CITATION DEMO (F4)
          ============================================================ */}
      <section aria-labelledby="cite-heading" className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <h2 id="cite-heading" className="mb-3 text-center text-3xl font-bold md:text-4xl">
          See exactly where the answer came from
        </h2>
        <p className="mx-auto mb-10 max-w-xl text-center" style={{ color: "var(--text-secondary)" }}>
          Move your cursor over the page to find the passage the answer cites.
        </p>
        <Flashlight />
      </section>

      {/* ============================================================
          GUARANTEES (F16) -- cards stack as you scroll
          ============================================================ */}
      <section aria-labelledby="trust-heading" className="px-4 py-16 sm:px-6">
        <h2 id="trust-heading" className="mb-10 text-center text-3xl font-bold md:text-4xl">
          Answers you can check
        </h2>
        <StackingCards items={GUARANTEES} />
        <div className="mt-12 flex justify-center">
          <Link
            href={signedIn ? "/dashboard" : "/login"}
            className="btn-primary px-6 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2"
          >
            {signedIn ? "Open dashboard" : "Start asking questions"}
            <ArrowRight size={17} aria-hidden="true" />
          </Link>
        </div>
      </section>

      {/* ============================================================
          FOOTER
          ============================================================ */}
      <footer
        className="mx-auto max-w-6xl border-t px-4 py-10 text-center text-sm sm:px-6"
        style={{
          borderColor: "var(--border-subtle)",
          color: "var(--text-secondary)",
        }}
      >
        Built with FastAPI · PostgreSQL + pgvector · Celery · Next.js ·
        sentence-transformers · GPT-OSS 120B on Groq
      </footer>
    </main>
  );
}
