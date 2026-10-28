// Page / -- the first thing a visitor sees.

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

import LanguageSwitcher from "@/components/app/LanguageSwitcher";
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
import { useT } from "@/lib/i18n";

const fadeUp = {
  hidden: { opacity: 0, y: 24 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, ease: [0.22, 1, 0.36, 1] as const },
  },
};

const stagger = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.09, delayChildren: 0.15 },
  },
};

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

const HEADLINE = ["Ask your documents", "anything."];

const STATS = [
  { value: "19", label: "landing.stat.extensions" },
  { value: "384", label: "landing.stat.dimensions" },
  { value: "3", label: "landing.stat.strategies" },
  { value: "<50ms", label: "landing.stat.search" },
];

function subscribeToTokenChanges(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

export default function LandingPage() {
  const signedIn = useSyncExternalStore(
    subscribeToTokenChanges,
    () => Boolean(getToken()),
    () => false
  );

  const { t } = useT();

  const reduced = usePrefersReducedMotion();
  const initial = reduced ? "visible" : "hidden";

  return (
    <main className="relative min-h-screen overflow-x-clip">
      <SmoothScroll />

      <section
        aria-label={t("landing.intro")}
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
              {t("landing.pill")}
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
            {t("landing.lede")}
          </motion.p>

          <motion.div
            variants={fadeUp}
            className="mt-10 flex flex-wrap items-center justify-center gap-3 md:justify-start"
          >
            <Link
              href={signedIn ? "/dashboard" : "/login"}
              className="btn-primary px-6 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              {signedIn ? t("landing.cta.dashboard") : t("landing.cta.start")}
              <ArrowRight size={17} aria-hidden="true" />
            </Link>
            <a
              href="https://github.com/dhanoliya-ji/DocMinds"
              target="_blank"
              rel="noopener noreferrer"
              className="btn-ghost px-6 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              {t("landing.cta.source")}
            </a>
            <Link
              href="/developers"
              className="btn-ghost px-6 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              {t("landing.cta.developers")}
            </Link>
          </motion.div>
        </motion.div>

        <OrbHero />

        <ul className="grid grid-cols-2 gap-4 md:col-span-2 md:grid-cols-4">
          {STATS.map((stat) => (
            <li key={stat.label} className="glass px-4 py-5 text-center">
              <div className="text-2xl font-bold" style={{ color: "var(--accent-hover)" }}>
                {stat.value}
              </div>
              <div className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                {t(stat.label)}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="features-heading" className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <h2 id="features-heading" className="mb-10 text-center text-3xl font-bold md:text-4xl">
          {t("landing.features")}
        </h2>
        <ParallaxCards features={FEATURES} />
      </section>

      <PipelineScroll />

      <section aria-labelledby="cite-heading" className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <h2 id="cite-heading" className="mb-3 text-center text-3xl font-bold md:text-4xl">
          {t("landing.cite.title")}
        </h2>
        <p className="mx-auto mb-10 max-w-xl text-center" style={{ color: "var(--text-secondary)" }}>
          {t("landing.cite.body")}
        </p>
        <Flashlight />
      </section>

      <section aria-labelledby="trust-heading" className="px-4 py-16 sm:px-6">
        <h2 id="trust-heading" className="mb-10 text-center text-3xl font-bold md:text-4xl">
          {t("landing.trust")}
        </h2>
        <StackingCards items={GUARANTEES} />
        <div className="mt-12 flex justify-center">
          <Link
            href={signedIn ? "/dashboard" : "/login"}
            className="btn-primary px-6 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2"
          >
            {signedIn ? t("landing.cta.dashboard") : t("landing.cta.ask")}
            <ArrowRight size={17} aria-hidden="true" />
          </Link>
        </div>
      </section>

      <footer
        className="mx-auto max-w-6xl border-t px-4 py-10 text-center text-sm sm:px-6"
        style={{
          borderColor: "var(--border-subtle)",
          color: "var(--text-secondary)",
        }}
      >
        Built with FastAPI · PostgreSQL + pgvector · Celery · Next.js ·
        sentence-transformers · GPT-OSS 120B on Groq
        <div className="mt-3 flex flex-wrap items-center justify-center gap-4">
          <LanguageSwitcher />
          <Link href="/developers" className="underline" style={{ color: "var(--accent-hover)" }}>
            {t("landing.footer.developers")}
          </Link>
        </div>
      </footer>
    </main>
  );
}
