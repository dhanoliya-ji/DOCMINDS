/**
 * dashboard/page.tsx  (served at "/dashboard")
 * ============================================
 * Lists the organization's projects and lets you create new ones.
 *
 * WHAT IS A PROJECT?
 * ------------------
 * A folder for related documents, and — more importantly — a SEARCH BOUNDARY.
 * A question asked inside "HR Policies" only ever retrieves chunks from
 * documents uploaded to "HR Policies". That keeps answers relevant and stops
 * an engineering runbook from being quoted at someone asking about payroll.
 *
 * LAYOUT AND FIRST RUN
 * --------------------
 * The page sits inside <AppShell> (sidebar, theme toggle, sign-out, page
 * transition). A brand-new organization sees a first-run empty state with
 * sample project names -- one click opens the create dialog pre-filled --
 * and a three-step guided tour that is remembered once dismissed.
 */

"use client";

import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, FolderOpen, Loader2, Plus, Sparkles, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import AppShell from "@/components/app/AppShell";
import LogoLoader from "@/components/app/LogoLoader";
import { FirstRunEmpty, GuidedTour, type TourStep } from "@/components/app/Onboarding";
import ProjectCard from "@/components/app/ProjectCard";
import VerifyBanner from "@/components/VerifyBanner";
import { Project, api, getErrorMessage, getToken } from "@/lib/api";
import { rich, useT } from "@/lib/i18n";
import { canEdit } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

/** Sample names offered on first run (i18n keys); clicking one pre-fills the dialog. */
const SAMPLE_PROJECTS = [1, 2, 3, 4].map((n) => `dashboard.sample.${n}`);

/** The tour's steps, as i18n keys (lib/i18n/dashboard.ts). */
const TOUR_STEPS = [1, 2, 3];

export default function DashboardPage() {
  const router = useRouter();
  const { t } = useT();
  const tour: TourStep[] = TOUR_STEPS.map((n) => ({
    title: t(`dashboard.tour.${n}.title`),
    body: t(`dashboard.tour.${n}.body`),
  }));

  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // "Create project" dialog state.
  const [showModal, setShowModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  // Whether the backend has a Groq key. Drives the demo-mode banner, so the
  // user understands why answers might look like raw excerpts.
  const [aiReady, setAiReady] = useState<boolean | null>(null);

  // Who is signed in. Drives two things: the "confirm your email" banner,
  // and whether "New project" is offered at all -- creating a project needs
  // the editor role, so a viewer would only ever get a 403 from it. Until the
  // role is known the button stays hidden (fail closed; the backend decides).
  const { user } = useCurrentUser();
  const mayCreate = canEdit(user?.role);

  // ------------------------------------------------------------------
  // LOAD DATA ON FIRST RENDER
  // ------------------------------------------------------------------
  useEffect(() => {
    // Guard the page: without a token there is nothing to show, so redirect
    // to login rather than firing requests that will all fail with 401.
    if (!getToken()) {
      router.push("/login");
      return;
    }

    // Declared inside the effect because `useEffect` cannot itself be async.
    async function load() {
      try {
        // `Promise.all` runs both requests CONCURRENTLY rather than waiting for
        // the first to finish before starting the second. Two 100ms requests
        // take 100ms total instead of 200ms.
        const [projectList, health] = await Promise.all([
          api.listProjects(),
          // `.catch` on this one alone: a failing health check should not
          // prevent the projects from rendering.
          api.health().catch(() => null),
        ]);
        setProjects(projectList);
        setAiReady(health ? health.llm.api_key_configured : null);
      } catch (err) {
        setError(getErrorMessage(err, t("dashboard.loadError")));
      } finally {
        setLoading(false);
      }
    }

    load();
    // `router` is in the dependency list because the effect uses it. The
    // effect still runs only once, since the router object is stable.
    // `t` is left out on purpose: switching language must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  // ------------------------------------------------------------------
  // CREATE A PROJECT
  // ------------------------------------------------------------------
  async function handleCreate() {
    // `.trim()` so a name of only spaces is rejected.
    if (!newName.trim()) return;

    setCreating(true);
    try {
      const project = await api.createProject(newName.trim());
      // Put the newest project first, matching where the eye expects it.
      // We build a NEW array rather than pushing into the old one: React only
      // re-renders when it sees a different array reference.
      setProjects([project, ...projects]);
      setShowModal(false);
      setNewName("");
    } catch (err) {
      setError(getErrorMessage(err, t("dashboard.createError")));
    } finally {
      setCreating(false);
    }
  }

  function openCreate(prefill = "") {
    setNewName(prefill);
    setShowModal(true);
  }

  // ------------------------------------------------------------------
  // RENDER
  // ------------------------------------------------------------------
  return (
    <AppShell>
      {/* ---- Header ---- */}
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-gradient text-3xl font-bold">{t("dashboard.title")}</h1>
          <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
            {t("dashboard.subtitle")}
          </p>
        </div>
        {mayCreate && (
          <button onClick={() => openCreate()} className="btn-primary">
            <Plus size={16} aria-hidden="true" />
            {t("dashboard.newProject")}
          </button>
        )}
      </div>

      {/* ---- Email confirmation banner ---- */}
      {user && !user.is_verified && <VerifyBanner email={user.email} />}

      {/* ---- Demo-mode banner ----
          Shown only when the backend reports no Groq key. Explaining this
          up front prevents the user thinking the app is broken when the
          chat returns raw excerpts. */}
      {aiReady === false && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass mb-6 flex items-start gap-3 p-4"
          style={{ borderColor: "rgba(251,191,36,0.3)" }}
        >
          <Sparkles
            aria-hidden="true"
            size={17}
            className="mt-0.5 shrink-0"
            style={{ color: "var(--warning)" }}
          />
          <div className="text-sm">
            <div className="font-medium" style={{ color: "var(--warning)" }}>
              {t("dashboard.demo.title")}
            </div>
            <p className="mt-1" style={{ color: "var(--text-secondary)" }}>
              {rich(t("dashboard.demo.body"), {
                link: (
                  <a
                    href="https://console.groq.com/keys"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline"
                    style={{ color: "var(--accent-hover)" }}
                  >
                    console.groq.com/keys
                  </a>
                ),
                file: <code>backend/.env</code>,
                variable: <code>GROQ_API_KEY</code>,
              })}
            </p>
          </div>
        </motion.div>
      )}

      {/* ---- Error banner ---- */}
      {error && (
        <div
          role="alert"
          className="mb-6 flex items-center gap-2 rounded-xl px-4 py-3 text-sm"
          style={{
            background: "color-mix(in srgb, var(--danger) 10%, transparent)",
            color: "var(--danger)",
          }}
        >
          <AlertTriangle size={15} aria-hidden="true" />
          {error}
        </div>
      )}

      {/* ---- Content: loading, empty, or the grid ---- */}
      {loading ? (
        // F18: the logo-morph loader while the first page of data arrives.
        <LogoLoader label={t("dashboard.loading")} fullscreen />
      ) : projects.length === 0 ? (
        <>
          <GuidedTour id="dashboard" steps={tour} />
          <FirstRunEmpty
            icon={<FolderOpen size={26} />}
            title={t("dashboard.empty.title")}
            body={mayCreate ? t("dashboard.empty.editor") : t("dashboard.empty.viewer")}
            suggestionsLabel={mayCreate ? t("dashboard.empty.examples") : undefined}
            suggestions={mayCreate ? SAMPLE_PROJECTS.map((key) => t(key)) : undefined}
            onSuggestion={(name) => openCreate(name)}
            action={
              mayCreate && (
                <button onClick={() => openCreate()} className="btn-primary">
                  <Plus size={16} aria-hidden="true" />
                  {t("dashboard.createProject")}
                </button>
              )
            }
          />
        </>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project, index) => (
            <motion.div
              key={project.id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              // Stagger by position so the grid cascades in.
              transition={{ delay: Math.min(index, 8) * 0.04, duration: 0.3 }}
              className="min-w-0"
            >
              <ProjectCard project={project} index={index} />
            </motion.div>
          ))}
        </div>
      )}

      {/* ============================================================
          CREATE PROJECT DIALOG
          `AnimatePresence` is what allows an EXIT animation. Normally React
          removes an element from the DOM instantly; AnimatePresence keeps it
          mounted just long enough to animate out.
          ============================================================ */}
      <AnimatePresence>
        {showModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-6"
            style={{ background: "var(--overlay)", backdropFilter: "blur(6px)" }}
            // Clicking the dark backdrop closes the dialog, which users expect.
            onClick={() => setShowModal(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.94, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.94, y: 16 }}
              transition={{ duration: 0.22 }}
              role="dialog"
              aria-modal="true"
              aria-labelledby="new-project-heading"
              className="glass w-full max-w-md p-6"
              // Stop the click from reaching the backdrop handler above, which
              // would otherwise close the dialog whenever you clicked inside it.
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-5 flex items-center justify-between">
                <h2 id="new-project-heading" className="text-lg font-semibold">{t("dashboard.newProject")}</h2>
                <button
                  onClick={() => setShowModal(false)}
                  style={{ color: "var(--text-muted)" }}
                  aria-label={t("dashboard.dialog.close")}
                >
                  <X size={18} aria-hidden="true" />
                </button>
              </div>

              <input
                className="input"
                aria-label={t("dashboard.dialog.name")}
                placeholder={t("dashboard.dialog.placeholder")}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                // Let Enter submit, so the user need not reach for the mouse.
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                // Focus the field the moment the dialog opens.
                autoFocus
              />

              <div className="mt-5 flex justify-end gap-2">
                <button
                  onClick={() => setShowModal(false)}
                  className="btn-ghost"
                >
                  {t("dashboard.dialog.cancel")}
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating || !newName.trim()}
                  className="btn-primary"
                >
                  {creating ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Plus size={15} />
                  )}
                  {t("dashboard.dialog.create")}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </AppShell>
  );
}
