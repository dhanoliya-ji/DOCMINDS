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
 */

"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  FolderOpen,
  Loader2,
  LogOut,
  Plus,
  Settings,
  Sparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import VerifyBanner from "@/components/VerifyBanner";
import { Project, api, getErrorMessage, getToken } from "@/lib/api";
import { canEdit } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

export default function DashboardPage() {
  const router = useRouter();

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
        setError(getErrorMessage(err, "Could not load your projects."));
      } finally {
        setLoading(false);
      }
    }

    load();
    // `router` is in the dependency list because the effect uses it. The
    // effect still runs only once, since the router object is stable.
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
      setError(getErrorMessage(err, "Could not create the project."));
    } finally {
      setCreating(false);
    }
  }

  function handleLogout() {
    api.logout();
    router.push("/login");
  }

  // ------------------------------------------------------------------
  // RENDER
  // ------------------------------------------------------------------
  return (
    <main className="mx-auto min-h-screen max-w-6xl px-6 py-10">
      {/* ---- Header ---- */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-gradient text-3xl font-bold">Projects</h1>
          <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
            Each project is its own document collection and search boundary.
          </p>
        </div>
        <div className="flex gap-2">
          {mayCreate && (
            <button onClick={() => setShowModal(true)} className="btn-primary">
              <Plus size={16} aria-hidden="true" />
              New project
            </button>
          )}
          <Link href="/settings" className="btn-ghost" title="Settings" aria-label="Settings">
            <Settings size={16} aria-hidden="true" />
          </Link>
          <button onClick={handleLogout} className="btn-ghost" title="Sign out" aria-label="Sign out">
            <LogOut size={16} aria-hidden="true" />
          </button>
        </div>
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
            size={17}
            className="mt-0.5 shrink-0"
            style={{ color: "var(--warning)" }}
          />
          <div className="text-sm">
            <div className="font-medium" style={{ color: "var(--warning)" }}>
              Running in demo mode
            </div>
            <p className="mt-1" style={{ color: "var(--text-secondary)" }}>
              Upload, OCR, chunking and semantic search all work fully. Chat
              returns the best matching passage directly instead of an
              AI-written summary. To enable AI answers, add a free key from{" "}
              <a
                href="https://console.groq.com/keys"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
                style={{ color: "var(--accent-hover)" }}
              >
                console.groq.com/keys
              </a>{" "}
              to <code>backend/.env</code> as <code>GROQ_API_KEY</code>.
            </p>
          </div>
        </motion.div>
      )}

      {/* ---- Error banner ---- */}
      {error && (
        <div
          className="mb-6 flex items-center gap-2 rounded-xl px-4 py-3 text-sm"
          style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
        >
          <AlertTriangle size={15} />
          {error}
        </div>
      )}

      {/* ---- Content: loading, empty, or the grid ---- */}
      {loading ? (
        // Skeleton placeholders rather than a spinner. They show the SHAPE of
        // what is coming, which makes the wait feel shorter and stops the
        // layout jumping when the real data arrives.
        <div className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="glass shimmer h-32" />
          ))}
        </div>
      ) : projects.length === 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="glass flex flex-col items-center justify-center px-6 py-20 text-center"
        >
          <FolderOpen
            size={40}
            style={{ color: "var(--text-muted)" }}
            className="mb-4"
          />
          <h3 className="text-lg font-semibold">No projects yet</h3>
          <p
            className="mt-2 max-w-sm text-sm"
            style={{ color: "var(--text-secondary)" }}
          >
            {mayCreate
              ? "Create your first project, then upload documents into it to start asking questions."
              : "Your organization has no projects yet. Ask an editor or admin to create one."}
          </p>
          {mayCreate && (
            <button
              onClick={() => setShowModal(true)}
              className="btn-primary mt-6"
            >
              <Plus size={16} aria-hidden="true" />
              Create a project
            </button>
          )}
        </motion.div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {projects.map((project, index) => (
            <motion.button
              key={project.id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              // Stagger by position so the grid cascades in.
              transition={{ delay: index * 0.05, duration: 0.4 }}
              onClick={() => router.push(`/project/${project.id}`)}
              className="glass glass-hover p-5 text-left"
            >
              <div
                className="mb-3 inline-flex h-9 w-9 items-center justify-center rounded-lg"
                style={{
                  background: "rgba(99,102,241,0.14)",
                  color: "var(--accent-hover)",
                }}
              >
                <FolderOpen size={17} />
              </div>
              <h3 className="font-semibold">{project.name}</h3>
              <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                {/* Turn the ISO timestamp from the API into a readable local
                    date, e.g. "30 Aug 2026". */}
                Created{" "}
                {new Date(project.created_at).toLocaleDateString(undefined, {
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                })}
              </p>
            </motion.button>
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
            style={{ background: "rgba(3,6,15,0.75)", backdropFilter: "blur(6px)" }}
            // Clicking the dark backdrop closes the dialog, which users expect.
            onClick={() => setShowModal(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.94, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.94, y: 16 }}
              transition={{ duration: 0.22 }}
              className="glass w-full max-w-md p-6"
              // Stop the click from reaching the backdrop handler above, which
              // would otherwise close the dialog whenever you clicked inside it.
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-5 flex items-center justify-between">
                <h2 className="text-lg font-semibold">New project</h2>
                <button
                  onClick={() => setShowModal(false)}
                  style={{ color: "var(--text-muted)" }}
                >
                  <X size={18} />
                </button>
              </div>

              <input
                className="input"
                placeholder="e.g. HR Policies, Q3 Contracts"
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
                  Cancel
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
                  Create
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}
