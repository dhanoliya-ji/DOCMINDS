/**
 * project/[id]/page.tsx  (served at "/project/<some-uuid>")
 * =========================================================
 * The main workspace: upload documents, watch them process, then search or
 * question them.
 *
 * WHAT THE SQUARE BRACKETS IN THE FOLDER NAME MEAN
 * ------------------------------------------------
 * `[id]` is a DYNAMIC ROUTE segment. One file serves every project:
 *
 *     /project/abc-123  ->  params.id === "abc-123"
 *     /project/def-456  ->  params.id === "def-456"
 *
 * Next.js reads the folder name and passes the matching URL piece in as
 * `params`, so we do not write a separate page per project.
 *
 * Since Next.js 15 `params` arrives as a PROMISE, unwrapped below with
 * React's `use()`.
 *
 * THE POLLING LOOP
 * ----------------
 * Processing happens in a background Celery worker, so the browser is never
 * told when it finishes. We therefore POLL: while any document is still
 * "pending" or "processing", re-fetch the list every three seconds. Once
 * everything has settled, the polling stops on its own so we are not making
 * pointless requests forever.
 *
 * LAYOUT AND FIRST RUN
 * --------------------
 * Wrapped in <AppShell>. A project with no documents shows a first-run empty
 * state (what to upload, what to ask) and a short guided tour, remembered
 * once dismissed. Document rows lift and unfold upload details on hover
 * (F12) and their icons sharpen in from a blur (F14).
 */

"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  FileUp,
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  MessageSquare,
  Search,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useState } from "react";

import AppShell from "@/components/app/AppShell";
import LogoLoader from "@/components/app/LogoLoader";
import { FirstRunEmpty, GuidedTour, type TourStep } from "@/components/app/Onboarding";
import ChatPanel from "@/components/ChatPanel";
import SearchPanel from "@/components/SearchPanel";
import UploadZone from "@/components/UploadZone";
import { api, DocumentItem, getToken } from "@/lib/api";
import { canEdit } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

/** The three tabs of the workspace. */
type Tab = "documents" | "search" | "chat";

/** Example files shown on a fresh project, to suggest what is worth uploading. */
const SAMPLE_UPLOADS = ["Employee handbook.pdf", "Contract.docx", "Pricing.xlsx", "Scanned receipt.jpg"];

const TOUR: TourStep[] = [
  {
    title: "Drop documents in",
    body: "Drag files onto the upload area. 19 formats are supported, and scans are read with OCR.",
  },
  {
    title: "Watch them process",
    body: "Each file moves from Queued to Processing to Ready. This page refreshes on its own.",
  },
  {
    title: "Then search or ask",
    body: "Use the Search tab for matching passages, or Ask AI for answers with page-level citations.",
  },
];

export default function ProjectPage({
  params,
}: {
  // A PROMISE, not a plain object.
  //
  // Next.js 15 made the dynamic route params async, so that a page can begin
  // rendering before the router has finished resolving them. Next 16 still
  // accepts synchronous access through a compatibility shim -- the page works
  // and returns 200 -- but logs an error in development and will break in a
  // future major.
  //
  // Worth knowing how that was found: `next build` type-checked it without
  // complaint and every unit test passed, because a test supplies `params`
  // itself. Only running `next dev` and requesting the page surfaced it.
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();

  // The signed-in user's role decides whether the upload zone is offered.
  const { user, loading: userLoading } = useCurrentUser();
  const mayUpload = canEdit(user?.role);

  // `use()` unwraps a promise inside a component. This is a Client Component,
  // so `await` is not available -- `use` is React's equivalent, and it
  // suspends the render until the value is ready.
  const projectId = use(params).id;

  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [projectName, setProjectName] = useState("Project");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("documents");

  // ------------------------------------------------------------------
  // FETCH THE DOCUMENT LIST
  // ------------------------------------------------------------------
  // Wrapped in `useCallback` so the function keeps the same identity between
  // renders. Without that, the polling effect below would see a "new" function
  // every render and restart its timer endlessly.
  const loadDocuments = useCallback(async () => {
    try {
      const list = await api.listDocuments(projectId);
      setDocuments(list);
    } catch {
      // Ignore transient failures: the poll will simply try again shortly.
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // ---- Initial load, plus a guard for signed-out users ----
  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }

    // eslint-disable-next-line react-hooks/set-state-in-effect -- see below
    loadDocuments();
    //
    // The rule above objects to setState inside an effect, and it is right to
    // in general: a synchronous setState in an effect body causes a second
    // render immediately after the first.
    //
    // That is not what happens here. `loadDocuments` is async, so its
    // setDocuments and setLoading run in promise callbacks after the fetch
    // resolves -- the effect body itself sets no state. The rule flags the
    // call site because it cannot see through the async boundary.
    //
    // Fetching on mount is also the one thing an effect is unambiguously for.
    // Avoiding it would mean adopting a data-fetching library (TanStack Query
    // or similar), which is a reasonable thing to want and a much larger
    // change than this warrants.

    // Look up this project's name for the page heading. The list endpoint is
    // reused rather than adding a dedicated "get one project" call.
    api
      .listProjects()
      .then((projects) => {
        const match = projects.find((p) => p.id === projectId);
        if (match) setProjectName(match.name);
      })
      .catch(() => {
        /* a missing title is not worth surfacing as an error */
      });
  }, [projectId, router, loadDocuments]);

  // ---- The polling loop ----
  useEffect(() => {
    // Is anything still being worked on?
    const stillWorking = documents.some(
      (doc) => doc.status === "pending" || doc.status === "processing"
    );

    // Everything has settled — do not schedule another check.
    if (!stillWorking) return;

    const timer = setInterval(loadDocuments, 3000);

    // CLEANUP. React runs this when the effect re-runs or the component is
    // removed. Without it, every re-render would stack up another interval and
    // the app would slowly bombard the server.
    return () => clearInterval(timer);
  }, [documents, loadDocuments]);

  // ------------------------------------------------------------------
  // SMALL HELPERS
  // ------------------------------------------------------------------

  /** Turn a byte count into something human, e.g. 1261 -> "1.2 KB". */
  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  /** Choose the icon and pill style for each processing state. */
  function statusBadge(status: string) {
    switch (status) {
      case "completed":
        return {
          className: "pill-success",
          icon: <CheckCircle2 size={11} />,
          label: "Ready",
        };
      case "processing":
        return {
          className: "pill-info",
          icon: <Loader2 size={11} className="animate-spin" />,
          label: "Processing",
        };
      case "failed":
        return {
          className: "pill-danger",
          icon: <XCircle size={11} />,
          label: "Failed",
        };
      default:
        return {
          className: "pill-warning",
          icon: <Clock size={11} />,
          label: "Queued",
        };
    }
  }

  // How many documents are actually searchable, which decides whether the
  // search and chat tabs have anything to work with.
  const readyCount = documents.filter((d) => d.status === "completed").length;

  return (
    <AppShell>
      {/* ---- Header ---- */}
      <Link
        href="/dashboard"
        className="mb-5 inline-flex items-center gap-1.5 text-sm transition-colors"
        style={{ color: "var(--text-secondary)" }}
      >
        <ArrowLeft size={14} aria-hidden="true" />
        All projects
      </Link>

      <div className="mb-6 min-w-0">
        <h1 className="text-gradient break-words text-3xl font-bold">{projectName}</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {documents.length} document{documents.length === 1 ? "" : "s"} ·{" "}
          {readyCount} ready to search
        </p>
      </div>

      {/* ---- Tabs ---- */}
      <div
        className="mb-6 flex gap-1 rounded-xl p-1"
        style={{ background: "var(--surface-1)" }}
      >
        {(
          [
            { key: "documents", label: "Documents", icon: FileText },
            { key: "search", label: "Search", icon: Search },
            { key: "chat", label: "Ask AI", icon: MessageSquare },
          ] as const
        ).map((item) => {
          const Icon = item.icon;
          const active = tab === item.key;
          return (
            <button
              key={item.key}
              onClick={() => setTab(item.key)}
              aria-pressed={active}
              className="relative flex min-w-0 flex-1 items-center justify-center gap-2 rounded-lg px-2 py-2.5 text-sm font-medium transition-colors sm:px-4"
              style={{
                color: active ? "#fff" : "var(--text-secondary)",
              }}
            >
              {/* The sliding highlight behind the active tab.
                  Giving this motion.div a shared `layoutId` is what makes
                  Framer Motion ANIMATE it between tabs rather than having it
                  disappear here and reappear there. */}
              {active && (
                <motion.div
                  layoutId="tab-highlight"
                  className="absolute inset-0 rounded-lg"
                  style={{
                    background:
                      "linear-gradient(135deg, var(--accent), var(--violet))",
                  }}
                  transition={{ type: "spring", stiffness: 380, damping: 30 }}
                />
              )}
              {/* `relative` lifts the label above the highlight behind it. */}
              <span className="relative flex items-center gap-2">
                <Icon size={15} aria-hidden="true" />
                {item.label}
              </span>
            </button>
          );
        })}
      </div>

      {/* ---- Tab contents ----
          `mode="wait"` makes the outgoing panel finish animating out before
          the incoming one animates in, avoiding the two overlapping. */}
      <AnimatePresence mode="wait">
        <motion.div
          // Changing the `key` is what tells AnimatePresence this is a
          // different element, triggering the exit/enter transition.
          key={tab}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.18 }}
        >
          {/* ================= DOCUMENTS ================= */}
          {tab === "documents" && (
            <div className="space-y-5">
              {/* Uploading needs the editor role. A viewer is told why the
                  zone is missing rather than offered one that answers 403.
                  While the role is still loading, show neither. */}
              {mayUpload ? (
                <UploadZone projectId={projectId} onUploaded={loadDocuments} />
              ) : (
                !userLoading && (
                  <p
                    className="glass px-4 py-3 text-sm"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    You have view-only access to this project. Ask an editor or
                    admin to upload documents.
                  </p>
                )
              )}

              {loading ? (
                <LogoLoader label="Loading documents" />
              ) : documents.length === 0 ? (
                <>
                  <GuidedTour id="project" steps={TOUR} />
                  <FirstRunEmpty
                    icon={<FileUp size={26} />}
                    title="No documents yet"
                    body={
                      mayUpload
                        ? "Upload one above to get started. Good first picks are documents people ask about often."
                        : "Nothing has been uploaded to this project yet."
                    }
                    suggestionsLabel="For example"
                    suggestions={SAMPLE_UPLOADS}
                  />
                </>
              ) : (
                <div className="space-y-2">
                  {documents.map((doc, index) => {
                    const badge = statusBadge(doc.status);
                    return (
                      <motion.div
                        key={doc.id}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="glass lift-card flex items-start gap-4 p-4"
                      >
                        <div
                          className="blur-reveal flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
                          style={{
                            background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                            color: "var(--accent-hover)",
                            ["--reveal-delay" as string]: `${Math.min(index, 8) * 50}ms`,
                          }}
                          aria-hidden="true"
                        >
                          <FileText size={16} />
                        </div>

                        <div className="min-w-0 flex-1">
                          {/* `truncate` clips a long filename with an ellipsis
                              instead of letting it break the layout. */}
                          <div className="truncate text-sm font-medium">
                            {doc.filename}
                          </div>
                          <div
                            className="mt-0.5 flex flex-wrap gap-2 text-xs"
                            style={{ color: "var(--text-muted)" }}
                          >
                            <span className="uppercase">{doc.file_type}</span>
                            <span>· {formatSize(doc.file_size)}</span>
                            {/* Facts recorded by the ingestion pipeline. They
                                only exist once processing has completed. */}
                            {doc.meta_data?.chunk_count && (
                              <span>· {doc.meta_data.chunk_count} chunks</span>
                            )}
                            {doc.meta_data?.word_count && (
                              <span>· {doc.meta_data.word_count} words</span>
                            )}
                            {doc.meta_data?.needs_ocr && (
                              <span style={{ color: "var(--warning)" }}>
                                · OCR applied
                              </span>
                            )}
                            {doc.meta_data?.processing_seconds && (
                              <span>
                                · processed in{" "}
                                {doc.meta_data.processing_seconds}s
                              </span>
                            )}
                          </div>
                          {/* Show the real reason a document failed, rather
                              than leaving the user to guess. `error_message`
                              is the backend's field for it; meta_data.error
                              is the older copy of the same text. */}
                          {doc.status === "failed" &&
                            (doc.error_message || doc.meta_data?.error) && (
                              <div
                                className="mt-1 text-xs"
                                style={{ color: "var(--danger)" }}
                              >
                                {doc.error_message || doc.meta_data?.error}
                              </div>
                            )}
                          {/* A temporary failure (a network blip, a rate
                              limit) sends the document back to the queue to
                              be retried. Saying so explains why a "Queued"
                              document is taking its time. */}
                          {doc.status === "pending" && doc.error_message && (
                            <div
                              className="mt-1 text-xs"
                              style={{ color: "var(--warning)" }}
                            >
                              Retrying after a temporary problem: {doc.error_message}
                            </div>
                          )}
                          {/* F12 hover preview: when it was added. */}
                          <div className="card-preview">
                            <div>
                              <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                                Uploaded {new Date(doc.created_at).toLocaleString()}
                              </div>
                            </div>
                          </div>
                        </div>

                        <span className={`${badge.className} shrink-0`}>
                          {badge.icon}
                          {badge.label}
                        </span>
                      </motion.div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ================= SEARCH ================= */}
          {tab === "search" &&
            (readyCount === 0 ? (
              <EmptyPrompt
                message="Upload a document and wait for it to finish processing before searching."
              />
            ) : (
              <SearchPanel projectId={projectId} />
            ))}

          {/* ================= CHAT ================= */}
          {tab === "chat" &&
            (readyCount === 0 ? (
              <EmptyPrompt
                message="Upload a document and wait for processing to finish, then ask questions about it."
              />
            ) : (
              <ChatPanel projectId={projectId} />
            ))}
        </motion.div>
      </AnimatePresence>
    </AppShell>
  );
}

/**
 * A small shared placeholder shown when a tab has no data to work with yet.
 * Defined here rather than in its own file because it is only used twice, and
 * both uses are on this page.
 */
function EmptyPrompt({ message }: { message: string }) {
  return (
    <div
      className="glass px-6 py-16 text-center text-sm"
      style={{ color: "var(--text-secondary)" }}
    >
      {message}
    </div>
  );
}
