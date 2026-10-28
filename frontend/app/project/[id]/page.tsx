// Page /project/<some-uuid> -- the main workspace: upload documents, watch them process, then search or...

"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  Check,
  GitCompare,
  Share2,
  FileUp,
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  MessageSquare,
  Pencil,
  Search,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useState } from "react";

import AppShell from "@/components/app/AppShell";
import LogoLoader from "@/components/app/LogoLoader";
import { FirstRunEmpty, GuidedTour, type TourStep } from "@/components/app/Onboarding";
import ChatPanel from "@/components/ChatPanel";
import ComparePanel from "@/components/ComparePanel";
import DocumentActions from "@/components/DocumentActions";
import SearchPanel from "@/components/SearchPanel";
import ShareDialog from "@/components/ShareDialog";
import UploadZone from "@/components/UploadZone";
import { api, DocumentItem, getErrorMessage, getToken } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { describeProgress, documentProgress } from "@/lib/progress";
import { canEdit } from "@/lib/roles";
import { useCurrentUser } from "@/lib/useCurrentUser";

type Tab = "documents" | "search" | "chat" | "compare";

const SAMPLE_UPLOADS = [1, 2, 3, 4].map((n) => `project.sample.${n}`);

const TOUR_STEPS = [1, 2, 3];

export default function ProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const { t, locale } = useT();
  const tour: TourStep[] = TOUR_STEPS.map((n) => ({
    title: t(`project.tour.${n}.title`),
    body: t(`project.tour.${n}.body`),
  }));

  const { user, loading: userLoading } = useCurrentUser();
  const mayUpload = canEdit(user?.role);

  const projectId = use(params).id;

  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("documents");
  const [actionError, setActionError] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [sharing, setSharing] = useState(false);

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

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }

    // eslint-disable-next-line react-hooks/set-state-in-effect -- see below
    loadDocuments();
  }, [projectId, router, loadDocuments]);

  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    api
      .getProject(projectId)
      .then((project) => !cancelled && setProjectName(project.name))
      .catch(() => {
        /* a missing title is not worth surfacing as an error */
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    const stillWorking = documents.some(
      (doc) => doc.status === "pending" || doc.status === "processing"
    );

    if (!stillWorking) return;

    const timer = setInterval(loadDocuments, 3000);

    return () => clearInterval(timer);
  }, [documents, loadDocuments]);


  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function statusBadge(status: string) {
    switch (status) {
      case "completed":
        return {
          className: "pill-success",
          icon: <CheckCircle2 size={11} />,
          label: t("project.status.completed"),
        };
      case "processing":
        return {
          className: "pill-info",
          icon: <Loader2 size={11} className="animate-spin" />,
          label: t("project.status.processing"),
        };
      case "failed":
        return {
          className: "pill-danger",
          icon: <XCircle size={11} />,
          label: t("project.status.failed"),
        };
      default:
        return {
          className: "pill-warning",
          icon: <Clock size={11} />,
          label: t("project.status.pending"),
        };
    }
  }

  const readyDocuments = documents.filter((d) => d.status === "completed");
  const readyCount = readyDocuments.length;

  async function saveProjectName() {
    setRenaming(false);
    const name = nameDraft.trim();
    if (!name || name === projectName) return;
    try {
      const project = await api.renameProject(projectId, name);
      setProjectName(project.name);
    } catch (err) {
      setActionError(getErrorMessage(err, t("project.renameError")));
    }
  }

  async function deleteProject() {
    try {
      await api.deleteProject(projectId);
      router.push("/dashboard");
    } catch (err) {
      setConfirmDelete(false);
      setActionError(getErrorMessage(err, t("project.deleteError")));
    }
  }

  return (
    <AppShell>
      <Link
        href="/dashboard"
        className="mb-5 inline-flex items-center gap-1.5 text-sm transition-colors"
        style={{ color: "var(--text-secondary)" }}
      >
        <ArrowLeft size={14} aria-hidden="true" />
        {t("project.allProjects")}
      </Link>

      <div className="mb-6 min-w-0">
        {renaming ? (
          <div className="flex items-center gap-2">
            <input
              className="input max-w-md text-lg"
              value={nameDraft}
              autoFocus
              aria-label={t("project.nameLabel")}
              maxLength={255}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveProjectName();
                if (e.key === "Escape") setRenaming(false);
              }}
            />
            <button type="button" className="btn-ghost" onClick={saveProjectName} aria-label={t("project.saveName")}>
              <Check size={15} />
            </button>
            <button type="button" className="btn-ghost" onClick={() => setRenaming(false)} aria-label={t("project.cancelRename")}>
              <X size={15} />
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-gradient break-words text-3xl font-bold">
              {projectName ?? t("project.fallbackName")}
            </h1>
            {mayUpload && (
              <div className="flex items-center gap-1 text-xs" style={{ color: "var(--text-muted)" }}>
                <button
                  type="button"
                  className="rounded p-1.5 hover:bg-white/5"
                  onClick={() => {
                    setNameDraft(projectName ?? "");
                    setRenaming(true);
                  }}
                  aria-label={t("project.rename")}
                  title={t("project.rename")}
                >
                  <Pencil size={14} />
                </button>
                <button
                  type="button"
                  className="rounded p-1.5 hover:bg-white/5"
                  onClick={() => setSharing(true)}
                  aria-label="Share project"
                  title="Share project"
                >
                  <Share2 size={14} />
                </button>
                {confirmDelete ? (
                  <>
                    <button
                      type="button"
                      className="rounded px-2 py-1 font-medium"
                      style={{ color: "var(--danger)" }}
                      onClick={deleteProject}
                    >
                      {t("project.deleteConfirm")}
                    </button>
                    <button type="button" className="p-1" onClick={() => setConfirmDelete(false)} aria-label={t("project.keep")}>
                      <X size={13} />
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="rounded p-1.5 hover:bg-white/5"
                    onClick={() => setConfirmDelete(true)}
                    aria-label={t("project.delete")}
                    title={t("project.delete")}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            )}
          </div>
        )}
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {t(documents.length === 1 ? "project.count.one" : "project.count.other", {
            count: documents.length,
            ready: readyCount,
          })}
        </p>
      </div>

      <div
        className="mb-6 flex gap-1 rounded-xl p-1"
        style={{ background: "var(--surface-1)" }}
      >
        {(
          [
            { key: "documents", label: t("project.tab.documents"), icon: FileText },
            { key: "search", label: t("project.tab.search"), icon: Search },
            { key: "chat", label: t("project.tab.chat"), icon: MessageSquare },
            { key: "compare", label: t("project.tab.compare"), icon: GitCompare },
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
              <span className="relative flex items-center gap-2">
                <Icon size={15} aria-hidden="true" />
                {item.label}
              </span>
            </button>
          );
        })}
      </div>

      <AnimatePresence mode="wait">
        <motion.div
          key={tab}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.18 }}
        >
          {tab === "documents" && (
            <div className="space-y-5">
              {mayUpload ? (
                <UploadZone projectId={projectId} onUploaded={loadDocuments} />
              ) : (
                !userLoading && (
                  <p
                    className="glass px-4 py-3 text-sm"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    {t("project.viewOnly")}
                  </p>
                )
              )}

              {actionError && (
                <div
                  role="alert"
                  className="flex items-center justify-between rounded-xl px-4 py-2.5 text-sm"
                  style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
                >
                  <span>{actionError}</span>
                  <button onClick={() => setActionError("")} aria-label={t("project.dismissError")}>
                    <X size={14} />
                  </button>
                </div>
              )}

              {loading ? (
                <LogoLoader label={t("project.loading")} />
              ) : documents.length === 0 ? (
                <>
                  <GuidedTour id="project" steps={tour} />
                  <FirstRunEmpty
                    icon={<FileUp size={26} />}
                    title={t("project.empty.title")}
                    body={mayUpload ? t("project.empty.editor") : t("project.empty.viewer")}
                    suggestionsLabel={t("project.empty.examples")}
                    suggestions={SAMPLE_UPLOADS.map((key) => t(key))}
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
                          <div className="truncate text-sm font-medium">
                            {doc.filename}
                          </div>
                          <div
                            className="mt-0.5 flex flex-wrap gap-2 text-xs"
                            style={{ color: "var(--text-muted)" }}
                          >
                            <span className="uppercase">{doc.file_type}</span>
                            <span>· {formatSize(doc.file_size)}</span>
                            {doc.meta_data?.chunk_count && (
                              <span>· {t("project.meta.chunks", { count: doc.meta_data.chunk_count })}</span>
                            )}
                            {doc.meta_data?.word_count && (
                              <span>· {t("project.meta.words", { count: doc.meta_data.word_count })}</span>
                            )}
                            {doc.meta_data?.needs_ocr && (
                              <span style={{ color: "var(--warning)" }}>
                                · {t("project.meta.ocr")}
                              </span>
                            )}
                            {doc.meta_data?.processing_seconds && (
                              <span>
                                ·{" "}
                                {t("project.meta.processed", {
                                  seconds: doc.meta_data.processing_seconds,
                                })}
                              </span>
                            )}
                          </div>
                          {doc.status === "failed" &&
                            (doc.error_message || doc.meta_data?.error) && (
                              <div
                                className="mt-1 text-xs"
                                style={{ color: "var(--danger)" }}
                              >
                                {doc.error_message || doc.meta_data?.error}
                              </div>
                            )}
                          {(doc.status === "pending" || doc.status === "processing") &&
                            describeProgress(documentProgress(doc), undefined, locale) && (
                              <div
                                className="mt-1 text-xs"
                                style={{ color: "var(--text-muted)" }}
                                data-testid="document-progress"
                              >
                                {describeProgress(documentProgress(doc), undefined, locale)}
                              </div>
                            )}
                          {doc.status === "pending" &&
                            doc.error_message &&
                            documentProgress(doc)?.stage !== "paused" && (
                            <div
                              className="mt-1 text-xs"
                              style={{ color: "var(--warning)" }}
                            >
                              {t("project.retrying", { error: doc.error_message })}
                            </div>
                          )}
                          <div className="card-preview">
                            <div>
                              <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                                {t("project.uploaded", { date: new Date(doc.created_at).toLocaleString() })}
                              </div>
                            </div>
                          </div>
                        </div>

                        <span className={`${badge.className} shrink-0`}>
                          {badge.icon}
                          {badge.label}
                        </span>
                        {mayUpload && (
                          <DocumentActions
                            document={doc}
                            onChanged={() => {
                              setActionError("");
                              loadDocuments();
                            }}
                            onError={setActionError}
                          />
                        )}
                      </motion.div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {tab === "search" &&
            (readyCount === 0 ? (
              <EmptyPrompt message={t("project.searchEmpty")} />
            ) : (
              <SearchPanel projectId={projectId} documents={readyDocuments} />
            ))}

          {tab === "chat" &&
            (readyCount === 0 ? (
              <EmptyPrompt message={t("project.chatEmpty")} />
            ) : (
              <ChatPanel projectId={projectId} documents={readyDocuments} canShareOrgPrompts={mayUpload} />
            ))}

          {tab === "compare" && <ComparePanel documents={readyDocuments} />}
        </motion.div>
      </AnimatePresence>
      {sharing && <ShareDialog kind="project" targetId={projectId} onClose={() => setSharing(false)} />}
    </AppShell>
  );
}

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
