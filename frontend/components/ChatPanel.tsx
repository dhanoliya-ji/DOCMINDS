// The conversation interface — where the whole RAG system is finally used.

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
  Share2,
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

import AgentStepIndicator, { agentPhase } from "@/components/AgentStepIndicator";
import AnswerMarkdown from "@/components/AnswerMarkdown";
import ChatSidebar from "@/components/ChatSidebar";
import ConfidenceBadge from "@/components/ConfidenceBadge";
import DocumentFilter from "@/components/DocumentFilter";
import PromptLibrary from "@/components/PromptLibrary";
import ShareDialog from "@/components/ShareDialog";
import SourceViewer, { SourceRef } from "@/components/SourceViewer";
import {
  AgentStep,
  ChatMessageItem,
  ChatMode,
  ChatSessionItem,
  CitationItem,
  DocumentItem,
  ShareKind,
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
import { useT } from "@/lib/i18n";

interface Props {
  projectId: string;
  documents?: DocumentItem[];
  canShareOrgPrompts?: boolean;
}

const SUGGESTIONS = ["chat.suggestion.1", "chat.suggestion.2", "chat.suggestion.3"];

const MODES: {
  value: ChatMode;
  label: string;
  icon: typeof Zap;
  hint: string;
}[] = [
  {
    value: "rag",
    label: "chat.mode.rag",
    icon: Zap,
    hint: "chat.mode.rag.hint",
  },
  {
    value: "agent",
    label: "chat.mode.agent",
    icon: Telescope,
    hint: "chat.mode.agent.hint",
  },
];

function formatArgs(args: Record<string, unknown>): string {
  const text = Object.entries(args ?? {})
    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
    .join(", ");
  return text.length > 90 ? `${text.slice(0, 90)}...` : text;
}

function toggled(current: Set<string>, id: string): Set<string> {
  const next = new Set(current);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return next;
}

export default function ChatPanel({ projectId, documents = [], canShareOrgPrompts = false }: Props) {
  const { t } = useT();
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [draftId, setDraftId] = useState<string | null>(null);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const [mode, setMode] = useState<ChatMode>("rag");
  const [documentIds, setDocumentIds] = useState<string[]>([]);

  const [viewing, setViewing] = useState<SourceRef | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [sharing, setSharing] = useState<{ kind: ShareKind; id: string } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const nextTempId = useRef(0);
  const modeButtonRefs = useRef<Partial<Record<ChatMode, HTMLButtonElement | null>>>({});
  const abortRef = useRef<AbortController | null>(null);

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

  const validIds = documentIds.filter((id) => documents.some((d) => d.id === id));

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

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

  async function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || sending) return;

    setInput("");
    setError("");
    setSending(true);

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

    const updateDraft = (change: (message: ChatMessageItem) => ChatMessageItem) =>
      setMessages((current) => current.map((m) => (m.id === draft.id ? change(m) : m)));

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      let id = sessionId;
      if (!id) {
        const session = await api.createChatSession(projectId);
        id = session.id;
        setSessionId(id);
        setSessions((current) => [session, ...current]);
      }

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
          onReset: () => updateDraft((m) => ({ ...m, content: "" })),
        }
      );

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

  const title = sessions.find((s) => s.id === sessionId)?.title ?? t("chat.newConversation");

  function exportConversation() {
    downloadText(safeFilename(title), conversationToMarkdown(title, messages));
  }

  const draft = draftId ? messages.find((m) => m.id === draftId) : undefined;
  const waitingForFirstWord = !!draft && !draft.content && !draft.steps?.length;

  return (
    <div className="flex h-[calc(100vh-13rem)] gap-4">
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
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-ghost text-xs md:hidden"
            onClick={() => setHistoryOpen((v) => !v)}
            aria-expanded={historyOpen}
          >
            <History size={13} aria-hidden="true" /> {t("chat.history")}
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
            onClick={() => sessionId && setSharing({ kind: "session", id: sessionId })}
            disabled={!sessionId || messages.length === 0 || sending}
            title={t("share.conversation")}
          >
            <Share2 size={13} aria-hidden="true" /> {t("share.button")}
          </button>
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={exportConversation}
            disabled={messages.length === 0 || sending}
            title={t("chat.exportTitle")}
          >
            <Download size={13} aria-hidden="true" /> {t("chat.export")}
          </button>
        </div>

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
              <h3 className="text-lg font-semibold">{t("chat.emptyTitle")}</h3>
              <p className="mt-2 max-w-sm text-sm" style={{ color: "var(--text-secondary)" }}>
                {t("chat.emptyBody")}
              </p>

              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button key={suggestion} onClick={() => send(t(suggestion))} className="btn-ghost text-xs">
                    {t(suggestion)}
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
                className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}
              >
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
                      <div className="whitespace-pre-wrap">{message.content}</div>
                    ) : (
                      <AnswerMarkdown>{message.content}</AnswerMarkdown>
                    )}
                  </div>

                  {!isUser && (
                    <div className="mt-2">
                      <div className="flex flex-wrap items-center gap-3 text-xs" style={{ color: "var(--text-muted)" }}>
                        {message.mode && (
                          <span className="pill-info">
                            {message.mode === "agent" ? "Deep research" : "Quick answer"}
                          </span>
                        )}
                        {isDraft && <span>Writing...</span>}
                        {!isDraft && <ConfidenceBadge confidence={message.confidence} />}
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
                            {!message.id.startsWith("temp-") && (
                              <button
                                onClick={() => setSharing({ kind: "message", id: message.id })}
                                className="flex items-center gap-1 transition-colors"
                                title={t("share.answer")}
                                aria-label={t("share.answer")}
                              >
                                <Share2 size={12} />
                              </button>
                            )}
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

                        {messageSteps.length > 0 && !isDraft && (
                          <button
                            onClick={() => setExpandedSteps((c) => toggled(c, message.id))}
                            className="flex items-center gap-1 transition-colors"
                            style={{ color: "var(--accent-hover)" }}
                            aria-expanded={showSteps}
                          >
                            <ListChecks size={12} />
                            {t("chat.researchSteps")} ({messageSteps.length})
                            <ChevronDown
                              size={12}
                              className="transition-transform"
                              style={{ transform: showSteps ? "rotate(180deg)" : "rotate(0deg)" }}
                            />
                          </button>
                        )}

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

                      {isDraft && mode === "agent" && (
                        <AgentStepIndicator phase={agentPhase(messageSteps, message.content)} />
                      )}
                      <AnimatePresence>
                        {showSteps && messageSteps.length > 0 && (
                          <motion.ol
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            className="glass mt-2 space-y-2 overflow-hidden p-3 text-xs"
                            aria-label={t("chat.researchSteps")}
                          >
                            {messageSteps.map((step, index) => (
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

          <div ref={bottomRef} />
        </div>

        {error && (
          <div
            role="alert"
            className="mt-3 rounded-xl px-4 py-2.5 text-sm"
            style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
          >
            {error}
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <div
            role="radiogroup"
            aria-label={t("chat.modeGroup")}
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
                  disabled={sending}
                  className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-60"
                  style={{
                    background: active ? "rgba(99,102,241,0.18)" : "transparent",
                    color: active ? "var(--accent-hover)" : "var(--text-secondary)",
                  }}
                >
                  <Icon size={13} />
                  {t(label)}
                </button>
              );
            })}
          </div>
          <p id="chat-mode-hint" className="text-xs" style={{ color: "var(--text-muted)" }}>
            {t(MODES.find((m) => m.value === mode)?.hint ?? "")}
          </p>
        </div>

        <div className="mt-2 flex items-end gap-2">
          <PromptLibrary
            draft={input}
            canShare={canShareOrgPrompts}
            disabled={sending}
            onInsert={(body) => {
              setInput(body);
              inputRef.current?.focus();
            }}
          />
          <textarea
            ref={inputRef}
            className="input resize-none py-3"
            rows={1}
            placeholder={t("chat.placeholder")}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
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
              title={t("chat.stop")}
              aria-label={t("chat.stopLabel")}
            >
              <Square size={16} />
            </button>
          ) : (
            <button
              onClick={() => send()}
              disabled={!input.trim()}
              className="btn-primary h-[46px] w-[46px] shrink-0 px-0!"
              title={t("chat.send")}
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </div>

      {viewing && <SourceViewer source={viewing} onClose={() => setViewing(null)} />}
      {sharing && <ShareDialog kind={sharing.kind} targetId={sharing.id} onClose={() => setSharing(null)} />}
    </div>
  );
}

function ResearchingLabel() {
  const { t } = useT();
  return (
    <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
      {t("chat.researching")}
    </span>
  );
}

function ThinkingBubble({ agent }: { agent: boolean }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-3" aria-busy="true">
      <div
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg animate-pulse-glow"
        style={{ background: "rgba(99,102,241,0.16)", color: "var(--accent-hover)" }}
      >
        <Bot size={15} />
      </div>
      <div className="flex flex-col items-start">
      {agent && <AgentStepIndicator phase="search" />}
      <div className="flex items-center gap-1.5 rounded-2xl px-4 py-4" style={{ background: "rgba(255,255,255,0.045)" }}>
        <span className="typing-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent-hover)" }} />
        <span className="typing-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent-hover)" }} />
        <span className="typing-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent-hover)" }} />
        {agent && <ResearchingLabel />}
      </div>
      </div>
    </motion.div>
  );
}
