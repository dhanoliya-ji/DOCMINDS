/**
 * ChatPanel.tsx
 * =============
 * The conversation interface — where the whole RAG system is finally used.
 *
 * WHAT HAPPENS WHEN YOU SEND A MESSAGE
 * ------------------------------------
 * The frontend makes ONE request:
 *
 *     POST /api/v1/chat/sessions/{id}/messages/stream
 *     { "message": "...", "mode": "rag" | "agent", "document_ids": [...] }
 *
 * and the backend answers with a STREAM of Server-Sent Events instead of one
 * reply at the end (see api.streamQuestion in lib/api.ts):
 *
 *     citations  the sources, before any text cites them
 *     step       (Deep research) each tool call the moment the agent makes it
 *     token      the next few characters of the answer
 *     done       the saved question and answer, with their real ids
 *
 * So the answer appears word by word, and a Deep-research run shows "searched
 * for X... read handbook.pdf..." live instead of a spinner for twenty
 * seconds. The two modes, chosen with the toggle above the input box:
 *
 *   QUICK ANSWER ("rag")
 *     The fixed pipeline: rewrite a follow-up into a standalone search,
 *     search ONCE (by meaning and by exact words), paste the closest chunks
 *     into a prompt, make ONE language-model call. Fast, cheap, predictable.
 *
 *   DEEP RESEARCH ("agent")
 *     A Pydantic AI agent is handed tools (search the documents, list them,
 *     read a section...) and decides for itself which to call and how often.
 *     Better on hard questions, but slower and uses more of the rate limit.
 *
 * HISTORY
 * -------
 * The sidebar lists this project's conversations (ChatSidebar.tsx). Picking
 * one RESUMES it: its messages are loaded with their citations, mode, model
 * and research steps -- all saved by the backend -- so it looks exactly as it
 * did live. A conversation is only created when its first question is sent,
 * so opening the tab does not litter the history with empty "New Chat"s.
 *
 * WHY CITATIONS ARE THE POINT
 * ---------------------------
 * An AI answer you cannot verify is worth very little in a workplace. Every
 * assistant message carries expandable source cards (file, page, the text
 * and a similarity score), and "View" opens the document itself at the
 * cited page with the passage highlighted (SourceViewer.tsx).
 */

"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  Copy,
  Download,
  ExternalLink,
  FileText,
  History,
  ListChecks,
  Sparkles,
  Square,
  Telescope,
  ThumbsDown,
  ThumbsUp,
  User,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import ChatSidebar from "@/components/ChatSidebar";
import DocumentFilter from "@/components/DocumentFilter";
import SourceViewer, { SourceRef } from "@/components/SourceViewer";
import {
  AgentStep,
  ChatMessageItem,
  ChatMode,
  ChatSessionItem,
  CitationItem,
  DocumentItem,
  api,
  getErrorMessage,
} from "@/lib/api";
import {
  answerToMarkdown,
  conversationToMarkdown,
  copyText,
  downloadText,
  safeFilename,
} from "@/lib/export";

interface Props {
  projectId: string;
  /** The project's searchable documents, for the "only these files" filter. */
  documents?: DocumentItem[];
}

/** Example questions offered on an empty conversation, to get people started. */
const SUGGESTIONS = [
  "Summarise the key points of these documents",
  "What are the main policies described here?",
  "List any deadlines or dates mentioned",
];

/**
 * The two answer modes, as the toggle presents them.
 *
 * Kept as data rather than written out twice in JSX, so the label, the icon
 * and the one-line hint for each mode cannot drift apart.
 */
const MODES: {
  value: ChatMode;
  label: string;
  icon: typeof Zap;
  hint: string;
}[] = [
  {
    value: "rag",
    label: "Quick answer",
    icon: Zap,
    hint: "One search, one answer. Fastest; best for simple, direct questions.",
  },
  {
    value: "agent",
    label: "Deep research",
    icon: Telescope,
    hint: "Searches several times and combines documents. Slower; best for complex questions.",
  },
];

/**
 * Turn a tool call's arguments into one short, readable line.
 *
 *     { query: "annual leave", top_k: 5 }  ->  query: "annual leave", top_k: 5
 *
 * Long values are cut off: the point is to show WHAT the agent looked for at a
 * glance, not to dump every byte it sent.
 */
function formatArgs(args: Record<string, unknown>): string {
  const text = Object.entries(args ?? {})
    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
    .join(", ");
  return text.length > 90 ? `${text.slice(0, 90)}...` : text;
}

/**
 * Add `id` to a Set if it is missing, or remove it if present.
 *
 * Returns a NEW Set rather than mutating. React compares state by reference,
 * so mutating the existing Set in place would not re-render.
 */
function toggled(current: Set<string>, id: string): Set<string> {
  const next = new Set(current);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return next;
}

export default function ChatPanel({ projectId, documents = [] }: Props) {
  // ---- History ----
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  // null = a new conversation that has not been created on the server yet.
  const [sessionId, setSessionId] = useState<string | null>(null);
  // On a narrow screen the sidebar is a toggle rather than a column.
  const [historyOpen, setHistoryOpen] = useState(false);

  // ---- The conversation on screen ----
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // The id of the answer currently being streamed in, if any.
  const [draftId, setDraftId] = useState<string | null>(null);

  // Which citation blocks are expanded, keyed by message id. Stored as a Set
  // because membership tests are what we need.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Which step lists are expanded. Separate from `expanded` so opening the
  // steps does not also open the sources, and vice versa.
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  // Which mode the NEXT question is sent in. "rag" is the default because it
  // is the fast, cheap path and is all most questions need.
  //
  // Deliberately plain component state, not localStorage. This component is
  // rendered on the server first, where there is no localStorage; reading a
  // saved "agent" during the first client render would then disagree with
  // the server's HTML (a hydration mismatch).
  const [mode, setMode] = useState<ChatMode>("rag");
  // "Only these documents"; empty = the whole project.
  const [documentIds, setDocumentIds] = useState<string[]>([]);

  // The source open in the viewer, and which answer was just copied.
  const [viewing, setViewing] = useState<SourceRef | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  // The empty div at the bottom of the message list, used as a scroll target.
  const bottomRef = useRef<HTMLDivElement>(null);
  // A counter for the temporary ids of optimistic messages: monotonic, so
  // ids never collide, and reading a ref is pure (unlike Date.now()).
  const nextTempId = useRef(0);
  // The two mode buttons, so the arrow keys can move focus between them.
  const modeButtonRefs = useRef<Partial<Record<ChatMode, HTMLButtonElement | null>>>({});
  // Lets the Stop button cancel the request in flight.
  const abortRef = useRef<AbortController | null>(null);

  // ------------------------------------------------------------------
  // Load this project's conversation history.
  // ------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    api
      .listProjectChatSessions(projectId)
      .then((list) => !cancelled && setSessions(list))
      .catch((err) => !cancelled && setError(getErrorMessage(err, "Could not load your conversations.")))
      .finally(() => !cancelled && setSessionsLoading(false));
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Ignore a filter entry naming a document no longer in the list (deleted,
  // or being re-indexed): sending it would silently match nothing.
  const validIds = documentIds.filter((id) => documents.some((d) => d.id === id));

  // ------------------------------------------------------------------
  // Keep the newest message in view.
  // ------------------------------------------------------------------
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  // ------------------------------------------------------------------
  // History actions
  // ------------------------------------------------------------------
  async function openSession(id: string) {
    if (sending) return;
    setSessionId(id);
    setHistoryOpen(false);
    setMessages([]);
    setError("");
    try {
      setMessages(await api.listMessages(id));
    } catch (err) {
      setError(getErrorMessage(err, "Could not open that conversation."));
    }
  }

  function newChat() {
    if (sending) return;
    setSessionId(null);
    setMessages([]);
    setError("");
    setHistoryOpen(false);
  }

  async function renameSession(id: string, title: string) {
    try {
      const updated = await api.renameChatSession(id, title);
      setSessions((current) => current.map((s) => (s.id === id ? updated : s)));
    } catch (err) {
      setError(getErrorMessage(err, "Could not rename the conversation."));
    }
  }

  async function deleteSession(id: string) {
    try {
      await api.deleteChatSession(id);
      setSessions((current) => current.filter((s) => s.id !== id));
      if (id === sessionId) newChat();
    } catch (err) {
      setError(getErrorMessage(err, "Could not delete the conversation."));
    }
  }

  // ------------------------------------------------------------------
  // Send a question.
  // ------------------------------------------------------------------
  async function send(text?: string) {
    const question = (text ?? input).trim();
    // Ignore empty input, and ignore a second send while one is in flight.
    if (!question || sending) return;

    setInput("");
    setError("");
    setSending(true);

    // OPTIMISTIC UPDATE: show the user's own message immediately, plus an
    // empty assistant bubble the streamed answer is written into. Both are
    // replaced by the saved messages (with real ids) when the answer is
    // done, or removed again if it fails.
    const now = new Date().toISOString();
    const optimistic: ChatMessageItem = {
      id: `temp-${nextTempId.current++}`,
      session_id: sessionId ?? "",
      role: "user",
      content: question,
      tokens_used: null,
      latency: null,
      citations: [],
      created_at: now,
    };
    const draft: ChatMessageItem = {
      id: `draft-${nextTempId.current++}`,
      session_id: sessionId ?? "",
      role: "assistant",
      content: "",
      tokens_used: null,
      latency: null,
      citations: [],
      steps: [],
      mode,
      created_at: now,
    };
    setMessages((current) => [...current, optimistic, draft]);
    setDraftId(draft.id);

    // Apply a change to the draft bubble only.
    const updateDraft = (change: (message: ChatMessageItem) => ChatMessageItem) =>
      setMessages((current) => current.map((m) => (m.id === draft.id ? change(m) : m)));

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // A new conversation is created on its first question.
      let id = sessionId;
      if (!id) {
        const session = await api.createChatSession(projectId);
        id = session.id;
        setSessionId(id);
        setSessions((current) => [session, ...current]);
      }

      // `mode` and the filter are read here, at send time. The controls are
      // disabled while a request is in flight, so they cannot change
      // underneath us.
      const response = await api.streamQuestion(
        id,
        question,
        { mode, documentIds: validIds, signal: controller.signal },
        {
          onStart: (event) =>
            setSessions((current) =>
              current.map((s) => (s.id === event.session_id ? { ...s, title: event.title } : s))
            ),
          onStep: (step: AgentStep) => updateDraft((m) => ({ ...m, steps: [...(m.steps ?? []), step] })),
          onCitations: (citations) => updateDraft((m) => ({ ...m, citations })),
          onToken: (piece) => updateDraft((m) => ({ ...m, content: m.content + piece })),
        }
      );

      // Swap the placeholders for the real saved messages.
      setMessages((current) => [
        ...current.filter((m) => m.id !== optimistic.id && m.id !== draft.id),
        response.user_message,
        {
          ...response.assistant_message,
          citations: response.citations,
          steps: response.steps ?? response.assistant_message.steps ?? [],
          mode: response.mode ?? response.assistant_message.mode ?? mode,
          model: response.model ?? response.assistant_message.model,
        },
      ]);
      // The conversation just used rises to the top of the history.
      setSessions((current) => {
        const used = current.find((s) => s.id === id);
        return used
          ? [{ ...used, updated_at: new Date().toISOString() }, ...current.filter((s) => s.id !== id)]
          : current;
      });
    } catch (err) {
      const stopped = controller.signal.aborted;
      setError(
        stopped ? "Stopped. The answer was not saved." : getErrorMessage(err, "Could not get an answer.")
      );
      // Roll the placeholders back, so the transcript does not show a
      // question that was never answered.
      setMessages((current) => current.filter((m) => m.id !== optimistic.id && m.id !== draft.id));
    } finally {
      setSending(false);
      setDraftId(null);
      abortRef.current = null;
    }
  }

  async function rate(messageId: string, rating: number) {
    try {
      await api.sendFeedback(messageId, rating);
    } catch {
      // Feedback is a nice-to-have. A failure here should never interrupt the
      // conversation, so it is deliberately swallowed.
    }
  }

  async function copyAnswer(message: ChatMessageItem) {
    if (await copyText(answerToMarkdown(message))) {
      setCopied(message.id);
      setTimeout(() => setCopied((current) => (current === message.id ? null : current)), 2000);
    }
  }

  const title = sessions.find((s) => s.id === sessionId)?.title ?? "New conversation";

  function exportConversation() {
    downloadText(safeFilename(title), conversationToMarkdown(title, messages));
  }

  // The draft shows the typing dots until its first word (or step) arrives.
  const draft = draftId ? messages.find((m) => m.id === draftId) : undefined;
  const waitingForFirstWord = !!draft && !draft.content && !draft.steps?.length;

  return (
    <div className="flex h-[calc(100vh-13rem)] gap-4">
      {/* ============================================================
          HISTORY SIDEBAR
          ============================================================ */}
      <aside className={`${historyOpen ? "flex" : "hidden"} w-56 shrink-0 flex-col md:flex`}>
        <ChatSidebar
          sessions={sessions}
          activeId={sessionId}
          loading={sessionsLoading}
          disabled={sending}
          onSelect={openSession}
          onNew={newChat}
          onRename={renameSession}
          onDelete={deleteSession}
        />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* ---- Toolbar: title, filter, export ---- */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-ghost text-xs md:hidden"
            onClick={() => setHistoryOpen((v) => !v)}
            aria-expanded={historyOpen}
          >
            <History size={13} aria-hidden="true" /> History
          </button>
          <h2 className="min-w-0 flex-1 truncate text-sm font-medium" title={title}>
            {title}
          </h2>
          <DocumentFilter
            documents={documents}
            selected={validIds}
            onChange={setDocumentIds}
            disabled={sending}
          />
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={exportConversation}
            disabled={messages.length === 0 || sending}
            title="Download this conversation as Markdown"
          >
            <Download size={13} aria-hidden="true" /> Export
          </button>
        </div>

        {/* ============================================================
            MESSAGE LIST
            ============================================================ */}
        <div className="flex-1 space-y-5 overflow-y-auto pr-2">
          {messages.length === 0 && !sending && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex h-full flex-col items-center justify-center text-center"
            >
              <div
                className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl"
                style={{ background: "rgba(99,102,241,0.14)", color: "var(--accent-hover)" }}
              >
                <Sparkles size={24} />
              </div>
              <h3 className="text-lg font-semibold">Ask your documents</h3>
              <p className="mt-2 max-w-sm text-sm" style={{ color: "var(--text-secondary)" }}>
                Questions are answered only from the documents in this project,
                and every answer shows the page it came from.
              </p>

              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button key={suggestion} onClick={() => send(suggestion)} className="btn-ghost text-xs">
                    {suggestion}
                  </button>
                ))}
              </div>
            </motion.div>
          )}

          {messages.map((message) => {
            const isUser = message.role === "user";
            const isDraft = message.id === draftId;
            const showSources = expanded.has(message.id);
            const messageSteps = message.steps ?? [];
            // Live steps are shown open, so the research is visible as it happens.
            const showSteps = isDraft || expandedSteps.has(message.id);

            if (isDraft && waitingForFirstWord) {
              return <ThinkingBubble key={message.id} agent={mode === "agent"} />;
            }

            return (
              <motion.div
                key={message.id}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
                // User messages align right, assistant messages left.
                className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}
              >
                {/* Avatar */}
                <div
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
                  style={{
                    background: isUser ? "rgba(255,255,255,0.07)" : "rgba(99,102,241,0.16)",
                    color: isUser ? "var(--text-secondary)" : "var(--accent-hover)",
                  }}
                >
                  {isUser ? <User size={15} /> : <Bot size={15} />}
                </div>

                <div className={`max-w-[78%] ${isUser ? "items-end" : ""}`}>
                  {/* Bubble */}
                  <div
                    className="rounded-2xl px-4 py-3 text-sm leading-relaxed"
                    style={{
                      background: isUser
                        ? "linear-gradient(135deg, var(--accent), var(--violet))"
                        : "rgba(255,255,255,0.045)",
                      color: isUser ? "#fff" : "var(--text-primary)",
                    }}
                    aria-busy={isDraft || undefined}
                  >
                    {isUser ? (
                      // `whitespace-pre-wrap` preserves the user's own line breaks.
                      <div className="whitespace-pre-wrap">{message.content}</div>
                    ) : (
                      // The model writes Markdown, so we render it.
                      <div className="prose-invert space-y-2">
                        <ReactMarkdown>{message.content}</ReactMarkdown>
                      </div>
                    )}
                  </div>

                  {/* ---- Assistant footer: metrics, feedback, sources ---- */}
                  {!isUser && (
                    <div className="mt-2">
                      <div className="flex flex-wrap items-center gap-3 text-xs" style={{ color: "var(--text-muted)" }}>
                        {message.mode && (
                          <span className="pill-info">
                            {message.mode === "agent" ? "Deep research" : "Quick answer"}
                          </span>
                        )}
                        {isDraft && <span>Writing...</span>}
                        {message.model && !isDraft && <span title="Model">{message.model}</span>}
                        {message.latency !== null && message.latency !== undefined && (
                          <span>{message.latency}s</span>
                        )}
                        {message.tokens_used ? <span>{message.tokens_used} tokens</span> : null}

                        {!isDraft && (
                          <>
                            <button
                              onClick={() => copyAnswer(message)}
                              className="flex items-center gap-1 transition-colors"
                              title="Copy answer (Markdown, with sources)"
                              aria-label="Copy answer"
                            >
                              {copied === message.id ? <Check size={12} /> : <Copy size={12} />}
                              {copied === message.id ? "Copied" : null}
                            </button>
                            <button
                              onClick={() => rate(message.id, 1)}
                              className="transition-colors hover:text-emerald-400"
                              title="Helpful"
                            >
                              <ThumbsUp size={12} />
                            </button>
                            <button
                              onClick={() => rate(message.id, -1)}
                              className="transition-colors hover:text-red-400"
                              title="Not helpful"
                            >
                              <ThumbsDown size={12} />
                            </button>
                          </>
                        )}

                        {/* Toggle for the research steps (agent mode only) */}
                        {messageSteps.length > 0 && !isDraft && (
                          <button
                            onClick={() => setExpandedSteps((c) => toggled(c, message.id))}
                            className="flex items-center gap-1 transition-colors"
                            style={{ color: "var(--accent-hover)" }}
                            aria-expanded={showSteps}
                          >
                            <ListChecks size={12} />
                            Research steps ({messageSteps.length})
                            <ChevronDown
                              size={12}
                              className="transition-transform"
                              style={{ transform: showSteps ? "rotate(180deg)" : "rotate(0deg)" }}
                            />
                          </button>
                        )}

                        {/* Toggle for the source list */}
                        {message.citations?.length > 0 && (
                          <button
                            onClick={() => setExpanded((c) => toggled(c, message.id))}
                            className="flex items-center gap-1 transition-colors"
                            style={{ color: "var(--accent-hover)" }}
                          >
                            <FileText size={12} />
                            {message.citations.length} source
                            {message.citations.length > 1 ? "s" : ""}
                            <ChevronDown
                              size={12}
                              className="transition-transform"
                              style={{ transform: showSources ? "rotate(180deg)" : "rotate(0deg)" }}
                            />
                          </button>
                        )}
                      </div>

                      {/* ---- Research steps: live while streaming, then
                          collapsible. Above the citations, because the steps
                          are how the agent found its sources. ---- */}
                      <AnimatePresence>
                        {showSteps && messageSteps.length > 0 && (
                          <motion.ol
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            className="glass mt-2 space-y-2 overflow-hidden p-3 text-xs"
                            aria-label="Research steps"
                          >
                            {messageSteps.map((step, index) => (
                              // Index in the key: the same tool can be called
                              // twice with identical arguments.
                              <li key={`${index}-${step.tool}`} className="flex gap-2">
                                <span className="shrink-0 font-medium" style={{ color: "var(--text-muted)" }}>
                                  {index + 1}.
                                </span>
                                <div className="min-w-0">
                                  <code className="font-mono font-medium" style={{ color: "var(--accent-hover)" }}>
                                    {step.tool}
                                  </code>
                                  {Object.keys(step.args ?? {}).length > 0 && (
                                    <span className="ml-1.5 wrap-break-word" style={{ color: "var(--text-muted)" }}>
                                      ({formatArgs(step.args)})
                                    </span>
                                  )}
                                  <p style={{ color: "var(--text-secondary)" }}>{step.summary}</p>
                                </div>
                              </li>
                            ))}
                          </motion.ol>
                        )}
                      </AnimatePresence>

                      {/* ---- Expandable citation cards ---- */}
                      <AnimatePresence>
                        {showSources && (
                          <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            className="mt-2 space-y-2 overflow-hidden"
                          >
                            {message.citations.map((citation: CitationItem) => (
                              <div key={citation.chunk_id} className="glass p-3 text-xs">
                                <div className="mb-1.5 flex items-center justify-between gap-2">
                                  <span className="font-medium" style={{ color: "var(--accent-hover)" }}>
                                    [{citation.number}] {citation.filename}
                                    {citation.page_number ? ` · page ${citation.page_number}` : ""}
                                  </span>
                                  <span className="flex shrink-0 items-center gap-2">
                                    {/* The similarity score, so the user can judge match strength. */}
                                    <span className="pill-info">{Math.round(citation.score * 100)}% match</span>
                                    <button
                                      type="button"
                                      className="flex items-center gap-1"
                                      style={{ color: "var(--accent-hover)" }}
                                      onClick={() => setViewing(citation)}
                                      aria-label={`View source ${citation.number}`}
                                    >
                                      <ExternalLink size={12} /> View
                                    </button>
                                  </span>
                                </div>
                                <p className="leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                                  {citation.snippet}
                                  {citation.snippet.length >= 300 ? "..." : ""}
                                </p>
                              </div>
                            ))}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  )}
                </div>
              </motion.div>
            );
          })}

          {/* Invisible scroll anchor. */}
          <div ref={bottomRef} />
        </div>

        {/* ============================================================
            COMPOSER
            ============================================================ */}
        {/* A 429 (rate limit, or the monthly token quota) arrives already
            worded with WHEN to try again (getErrorMessage in lib/api.ts).
            role="alert" so a screen reader announces it. */}
        {error && (
          <div
            role="alert"
            className="mt-3 rounded-xl px-4 py-2.5 text-sm"
            style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
          >
            {error}
          </div>
        )}

        {/* ---- Answer mode toggle ----
            A segmented control acting as one radio group: ONE Tab stop
            (roving tabindex), arrow keys move the selection and the focus,
            and the hint is linked with aria-describedby. */}
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <div
            role="radiogroup"
            aria-label="Answer mode"
            aria-describedby="chat-mode-hint"
            className="inline-flex rounded-xl p-1"
            style={{ background: "rgba(255,255,255,0.045)" }}
            onKeyDown={(e) => {
              const step =
                e.key === "ArrowRight" || e.key === "ArrowDown"
                  ? 1
                  : e.key === "ArrowLeft" || e.key === "ArrowUp"
                    ? -1
                    : 0;
              if (step === 0 || sending) return;
              e.preventDefault();
              // Wrap around at either end, as the radio pattern specifies.
              const current = MODES.findIndex((m) => m.value === mode);
              const next = MODES[(current + step + MODES.length) % MODES.length].value;
              setMode(next);
              modeButtonRefs.current[next]?.focus();
            }}
          >
            {MODES.map(({ value, label, icon: Icon }) => {
              const active = mode === value;
              return (
                <button
                  key={value}
                  ref={(el) => {
                    modeButtonRefs.current[value] = el;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  tabIndex={active ? 0 : -1}
                  onClick={() => setMode(value)}
                  // Locked while a question is in flight, so the indicator
                  // always matches the request actually running.
                  disabled={sending}
                  className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-60"
                  style={{
                    background: active ? "rgba(99,102,241,0.18)" : "transparent",
                    color: active ? "var(--accent-hover)" : "var(--text-secondary)",
                  }}
                >
                  <Icon size={13} />
                  {label}
                </button>
              );
            })}
          </div>
          <p id="chat-mode-hint" className="text-xs" style={{ color: "var(--text-muted)" }}>
            {MODES.find((m) => m.value === mode)?.hint}
          </p>
        </div>

        <div className="mt-2 flex items-end gap-2">
          <textarea
            className="input resize-none py-3"
            rows={1}
            placeholder="Ask a question about these documents..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter inserts a newline.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            disabled={sending}
          />
          {sending ? (
            <button
              onClick={() => abortRef.current?.abort()}
              className="btn-ghost h-[46px] w-[46px] shrink-0 px-0!"
              title="Stop"
              aria-label="Stop answering"
            >
              <Square size={16} />
            </button>
          ) : (
            <button
              onClick={() => send()}
              disabled={!input.trim()}
              className="btn-primary h-[46px] w-[46px] shrink-0 px-0!"
              title="Send"
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </div>

      {viewing && <SourceViewer source={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

/** The "thinking" bubble shown until the first word (or step) arrives. */
function ThinkingBubble({ agent }: { agent: boolean }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-3" aria-busy="true">
      <div
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg animate-pulse-glow"
        style={{ background: "rgba(99,102,241,0.16)", color: "var(--accent-hover)" }}
      >
        <Bot size={15} />
      </div>
      <div className="flex items-center gap-1.5 rounded-2xl px-4 py-4" style={{ background: "rgba(255,255,255,0.045)" }}>
        {/* Three dots with staggered CSS animation delays (globals.css). */}
        <span className="typing-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent-hover)" }} />
        <span className="typing-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent-hover)" }} />
        <span className="typing-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent-hover)" }} />
        {/* Deep research can take much longer; saying so stops the wait
            reading as a hang. */}
        {agent && (
          <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
            Researching your documents...
          </span>
        )}
      </div>
    </motion.div>
  );
}
