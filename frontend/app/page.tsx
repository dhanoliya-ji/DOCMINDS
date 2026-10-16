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
  // during server rendering, which is what makes it safe here: there is no
  // localStorage on the server, so the server renders `false` and the browser
  // corrects it on hydration.
  //
  // The older `useState(false)` plus an effect did the same job with an extra
  // render pass, and React 19's linter flags it -- setting state synchronously
  // in an effect causes exactly the cascading render this hook avoids.
  const signedIn = useSyncExternalStore(
    subscribeToTokenChanges,
    () => Boolean(getToken()),
    // The server snapshot. Always false: a server-rendered page cannot know
    // whether this particular visitor is signed in.
    () => false
  );

  return (
    <main className="relative min-h-screen overflow-hidden">
      {/* ============================================================
          HERO
          ============================================================ */}
      <section className="mx-auto max-w-6xl px-6 pt-24 pb-16 md:pt-32">
        <motion.div
          // `initial`/`animate` play the animation immediately on page load.
          initial="hidden"
          animate="visible"
          variants={stagger}
          className="text-center"
        >
          {/* Small badge above the headline */}
          <motion.div variants={fadeUp} className="mb-6 flex justify-center">
            <span className="pill-info gap-2">
              <Sparkles size={13} />
              Retrieval-Augmented Generation over your own documents
            </span>
          </motion.div>

          <motion.h1
            variants={fadeUp}
            className="text-gradient animate-gradient mx-auto max-w-4xl text-5xl font-bold leading-[1.1] tracking-tight md:text-7xl md:leading-none"
          >
            Ask your documents
            <br />
            anything.
          </motion.h1>

          <motion.p
            variants={fadeUp}
            className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed"
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
            className="mt-10 flex flex-wrap items-center justify-center gap-3"
          >
            <Link
              href={signedIn ? "/dashboard" : "/login"}
              className="btn-primary px-6 py-3 text-base"
            >
              {signedIn ? "Open dashboard" : "Get started free"}
              <ArrowRight size={17} />
            </Link>
            <a
              href="https://github.com/dhanoliya-ji/DocMinds"
              target="_blank"
              // `noopener noreferrer` on any target="_blank" link: without it,
              // the opened page gets a handle back to this one and can
              // redirect it. This is a standard security precaution.
              rel="noopener noreferrer"
              className="btn-ghost px-6 py-3 text-base"
            >
              View source
            </a>
          </motion.div>

          {/* Stat strip */}
          <motion.div
            variants={fadeUp}
            className="mx-auto mt-16 grid max-w-3xl grid-cols-2 gap-4 md:grid-cols-4"
          >
            {STATS.map((stat) => (
              <div key={stat.label} className="glass px-4 py-5">
                <div
                  className="text-2xl font-bold"
                  style={{ color: "var(--accent-hover)" }}
                >
                  {stat.value}
                </div>
                <div
                  className="mt-1 text-xs"
                  style={{ color: "var(--text-muted)" }}
                >
                  {stat.label}
                </div>
              </div>
            ))}
          </motion.div>
        </motion.div>
      </section>

      {/* ============================================================
          FEATURE GRID
          ============================================================ */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <motion.div
          // `whileInView` waits until the section scrolls into view before
          // animating, so the cards animate as the user reaches them.
          initial="hidden"
          whileInView="visible"
          // `once: true` prevents replaying every time the user scrolls past,
          // which quickly becomes irritating.
          viewport={{ once: true, margin: "-80px" }}
          variants={stagger}
          className="grid gap-4 md:grid-cols-2 lg:grid-cols-3"
        >
          {FEATURES.map((feature) => {
            // Assigned to a capitalised variable because JSX treats a
            // lowercase tag as a literal HTML element rather than a component.
            const Icon = feature.icon;
            return (
              <motion.div
                key={feature.title}
                variants={fadeUp}
                className="glass glass-hover p-6"
              >
                <div
                  className="mb-4 inline-flex h-10 w-10 items-center justify-center rounded-xl"
                  style={{
                    background: "rgba(99,102,241,0.14)",
                    color: "var(--accent-hover)",
                  }}
                >
                  <Icon size={19} />
                </div>
                <h3 className="mb-2 font-semibold">{feature.title}</h3>
                <p
                  className="text-sm leading-relaxed"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {feature.body}
                </p>
              </motion.div>
            );
          })}
        </motion.div>
      </section>

      {/* ============================================================
          HOW IT WORKS — the RAG pipeline, in order
          ============================================================ */}
      <section className="mx-auto max-w-5xl px-6 py-16">
        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mb-12 text-center text-3xl font-bold"
        >
          How a question gets answered
        </motion.h2>

        <div className="space-y-3">
          {[
            {
              step: "01",
              name: "Extract",
              detail:
                "The file is routed to the right parser by extension. Scanned PDFs are detected by low text density and sent through OCR instead.",
            },
            {
              step: "02",
              name: "Chunk",
              detail:
                "Text is split into ~400-token pieces using tiktoken, page by page, with overlap so no sentence is lost at a boundary.",
            },
            {
              step: "03",
              name: "Embed",
              detail:
                "Each chunk becomes a 384-number vector describing its meaning, generated locally by all-MiniLM-L6-v2 and stored in pgvector.",
            },
            {
              step: "04",
              name: "Retrieve",
              detail:
                "Your question becomes a vector too. Postgres finds the closest chunks by cosine distance using an HNSW index, scoped to your organization.",
            },
            {
              step: "05",
              name: "Generate",
              detail:
                "Those excerpts are pasted into a prompt that forbids outside knowledge. Llama 3.3 70B writes the answer and cites each excerpt it used.",
            },
          ].map((item, index) => (
            <motion.div
              key={item.step}
              initial={{ opacity: 0, x: -20 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true }}
              // Each row is delayed slightly more than the last, so the list
              // cascades downward instead of appearing all together.
              transition={{ delay: index * 0.08, duration: 0.5 }}
              className="glass flex items-start gap-5 p-5"
            >
              <span
                className="font-mono text-sm font-bold"
                style={{ color: "var(--accent)" }}
              >
                {item.step}
              </span>
              <div>
                <h4 className="font-semibold">{item.name}</h4>
                <p
                  className="mt-1 text-sm leading-relaxed"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {item.detail}
                </p>
              </div>
            </motion.div>
          ))}
        </div>
      </section>

      {/* ============================================================
          FOOTER
          ============================================================ */}
      <footer
        className="mx-auto max-w-6xl border-t px-6 py-10 text-center text-sm"
        style={{
          borderColor: "var(--border-subtle)",
          color: "var(--text-muted)",
        }}
      >
        Built with FastAPI · PostgreSQL + pgvector · Celery · Next.js ·
        sentence-transformers · Llama 3.3 on Groq
      </footer>
    </main>
  );
}
