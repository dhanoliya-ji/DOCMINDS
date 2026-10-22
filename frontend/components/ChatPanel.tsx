/**
 * ChatPanel.tsx
 * =============
 * The conversation interface — where the whole RAG system is finally used.
 *
 * WHAT HAPPENS WHEN YOU SEND A MESSAGE
 * ------------------------------------
 * The frontend does very little of the work. It makes ONE request:
 *
 *     POST /api/v1/chat/sessions/{id}/messages
 *     { "message": "...", "mode": "rag" | "agent" }
 *
 * and the backend does everything else before replying. HOW it does it depends
 * on the mode picked with the toggle just above the input box:
 *
 *   QUICK ANSWER ("rag")
 *     The fixed pipeline: embed the question, search pgvector ONCE for the
 *     closest chunks, paste them into a prompt, make ONE language-model call,
 *     build the citations, save both messages. Fast, cheap, predictable.
 *
 *   DEEP RESEARCH ("agent")
 *     A Pydantic AI agent is handed tools (search the documents, list them,
 *     read a section...) and decides for itself which to call and how often.
 *     It can split a multi-part question, rewrite a search that missed, and
 *     combine several documents. Better on hard questions, but it makes
 *     several model calls, so it is slower and uses more of the rate limit.
 *
 * What comes back is the answer plus the list of sources that produced it,
 * which is what this component renders. In agent mode the reply ALSO lists the
 * tool calls the agent made ("research steps"), shown in a collapsible list so
 * you can see how it reached the answer.
 *
 * WHY THE STEPS LIVE IN A SEPARATE MAP
 * ------------------------------------
 * The backend does not save research steps -- only the answer and its
 * citations go into the database. So steps exist only on the live response,
 * and they are kept here in a map keyed by the assistant message's id rather
 * than squeezed onto the message object, whose shape mirrors what is stored.
 *
 * WHY CITATIONS ARE THE POINT
 * ---------------------------
 * An AI answer you cannot verify is worth very little in a workplace. Every
 * assistant message here carries expandable source cards showing the file, the
 * page and the actual text the answer was drawn from, plus a similarity score
 * so you can judge how strong the match was.
 */

"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowUp,
  Bot,
  ChevronDown,
  FileText,
  ListChecks,
  Sparkles,
  Telescope,
  ThumbsDown,
  ThumbsUp,
  User,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import {
  AgentStep,
  ChatMessageItem,
  ChatMode,
  CitationItem,
  api,
  getErrorMessage,
} from "@/lib/api";

interface Props {
  projectId: string;
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

export default function ChatPanel({ projectId }: Props) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  // Which citation blocks are expanded, keyed by message id. Stored as a Set
  // because membership tests are what we need, and a Set expresses that far
  // more clearly than an array with .includes().
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Which mode the NEXT question is sent in. "rag" is the default because it
  // is the fast, cheap path and is all most questions need.
  //
  // Deliberately plain component state, not localStorage. This component is
  // rendered on the server first, where there is no localStorage; reading a
  // saved "agent" during the first client render would then disagree with
  // the server's HTML (a hydration mismatch). Starting on "rag" every time is
  // the simpler, honest choice for what is really a per-question setting.
  const [mode, setMode] = useState<ChatMode>("rag");

  // Research steps for agent-mode answers, keyed by assistant message id.
  // See "WHY THE STEPS LIVE IN A SEPARATE MAP" at the top of this file.
  const [steps, setSteps] = useState<Record<string, AgentStep[]>>({});

  // Which step lists are expanded. Separate from `expanded` so opening the
  // steps does not also open the sources, and vice versa.
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  // A handle on the empty div at the bottom of the message list, used as a
  // scroll target.
  const bottomRef = useRef<HTMLDivElement>(null);

  // A counter for the temporary ids given to optimistic messages.
  //
  // This used to be `Date.now()`, which had two problems. Two messages created
  // in the same millisecond would share an id, and the rollback below removes
  // by id -- so a collision would discard the wrong message. And React 19's
  // linter rightly flags a clock read in component scope as impure, because it
  // cannot tell that `send` only ever runs from an event handler.
  //
  // A ref counter fixes both: it is monotonic, so ids never collide, and
  // reading a ref is pure. These ids are also purely local and short-lived --
  // each is replaced by the server's real id as soon as the reply lands.
  const nextTempId = useRef(0);
  // The two mode buttons, so the arrow keys can move focus between them
  // (see the Answer mode toggle near the composer).
  const modeButtonRefs = useRef<Partial<Record<ChatMode, HTMLButtonElement | null>>>({});

  // ------------------------------------------------------------------
  // Create a chat session when the panel first opens.
  // ------------------------------------------------------------------
  useEffect(() => {
    async function start() {
      try {
        const session = await api.createChatSession(projectId);
        setSessionId(session.id);
      } catch (err) {
        setError(getErrorMessage(err, "Could not start a conversation."));
      }
    }
    start();
    // Re-run if the user switches to a different project, so the conversation
    // is always scoped to the documents currently on screen.
  }, [projectId]);

  // ------------------------------------------------------------------
  // Keep the newest message in view.
  // ------------------------------------------------------------------
  useEffect(() => {
    // Runs after every change to the message list. `behavior: "smooth"` glides
    // rather than jumping, which makes a new answer easier to follow.
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  // ------------------------------------------------------------------
  // Send a question.
  // ------------------------------------------------------------------
  async function send(text?: string) {
    const question = (text ?? input).trim();
    // Ignore empty input, and ignore a second send while one is in flight.
    if (!question || !sessionId || sending) return;

    setInput("");
    setError("");
    setSending(true);

    // OPTIMISTIC UPDATE: show the user's own message immediately, before the
    // server has confirmed anything. Waiting for the round trip would make the
    // interface feel laggy, when in fact we already know exactly what the user
    // typed. The temporary id is replaced by the real one when the reply lands.
    const optimistic: ChatMessageItem = {
      id: `temp-${nextTempId.current++}`,
      session_id: sessionId,
      role: "user",
      content: question,
      tokens_used: null,
      latency: null,
      citations: [],
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);

    try {
      // `mode` is read here, at send time. The toggle is disabled while a
      // request is in flight, so it cannot change underneath us.
      const response = await api.askQuestion(sessionId, question, mode);

      // Keep the agent's tool calls, if any, for the steps list. Absent or
      // empty in rag mode, in which case nothing is stored.
      const agentSteps = response.steps ?? [];
      if (agentSteps.length > 0) {
        setSteps((current) => ({
          ...current,
          [response.assistant_message.id]: agentSteps,
        }));
      }

      // Swap the placeholder for the real saved message, then append the
      // assistant's answer.
      setMessages((current) => [
        ...current.filter((m) => m.id !== optimistic.id),
        response.user_message,
        { ...response.assistant_message, citations: response.citations },
      ]);
    } catch (err) {
      setError(getErrorMessage(err, "Could not get an answer."));
      // Roll the optimistic message back, so the transcript does not show a
      // question that was never actually answered.
      setMessages((current) => current.filter((m) => m.id !== optimistic.id));
    } finally {
      setSending(false);
    }
  }

  /**
   * Add `id` to a Set if it is missing, or remove it if present.
   *
   * Returns a NEW Set rather than mutating. React compares state by
   * reference, so mutating the existing Set in place would not re-render.
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

  /** Expand or collapse the sources under one assistant message. */
  function toggleCitations(messageId: string) {
    setExpanded((current) => toggled(current, messageId));
  }

  /** Expand or collapse the research steps under one assistant message. */
  function toggleSteps(messageId: string) {
    setExpandedSteps((current) => toggled(current, messageId));
  }

  async function rate(messageId: string, rating: number) {
    try {
      await api.sendFeedback(messageId, rating);
    } catch {
      // Feedback is a nice-to-have. A failure here should never interrupt the
      // conversation, so it is deliberately swallowed.
    }
  }

  return (
    <div className="flex h-[calc(100vh-13rem)] flex-col">
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
              style={{
                background: "rgba(99,102,241,0.14)",
                color: "var(--accent-hover)",
              }}
            >
              <Sparkles size={24} />
            </div>
            <h3 className="text-lg font-semibold">Ask your documents</h3>
            <p
              className="mt-2 max-w-sm text-sm"
              style={{ color: "var(--text-secondary)" }}
            >
              Questions are answered only from the documents in this project,
              and every answer shows the page it came from.
            </p>

            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => send(suggestion)}
                  className="btn-ghost text-xs"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </motion.div>
        )}

        {messages.map((message) => {
          const isUser = message.role === "user";
          const showSources = expanded.has(message.id);
          const messageSteps = steps[message.id] ?? [];
          const showSteps = expandedSteps.has(message.id);

          return (
            <motion.div
              key={message.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3 }}
              // User messages align right, assistant messages left — the
              // standard convention that lets you scan a conversation quickly.
              className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}
            >
              {/* Avatar */}
              <div
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
                style={{
                  background: isUser
                    ? "rgba(255,255,255,0.07)"
                    : "rgba(99,102,241,0.16)",
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
                >
                  {isUser ? (
                    // `whitespace-pre-wrap` preserves the user's own line
                    // breaks instead of collapsing them into one paragraph.
                    <div className="whitespace-pre-wrap">{message.content}</div>
                  ) : (
                    // The model writes Markdown — bullet lists, bold, headings
                    // — so we render it rather than showing raw asterisks.
                    <div className="prose-invert space-y-2">
                      <ReactMarkdown>{message.content}</ReactMarkdown>
                    </div>
                  )}
                </div>

                {/* ---- Assistant footer: metrics, feedback, sources ---- */}
                {!isUser && (
                  <div className="mt-2">
                    <div
                      className="flex flex-wrap items-center gap-3 text-xs"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {message.latency !== null && (
                        <span>{message.latency}s</span>
                      )}
                      {message.tokens_used ? (
                        <span>{message.tokens_used} tokens</span>
                      ) : null}

                      {/* Feedback buttons */}
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

                      {/* Toggle for the research steps (agent mode only) */}
                      {messageSteps.length > 0 && (
                        <button
                          onClick={() => toggleSteps(message.id)}
                          className="flex items-center gap-1 transition-colors"
                          style={{ color: "var(--accent-hover)" }}
                          aria-expanded={showSteps}
                        >
                          <ListChecks size={12} />
                          Research steps ({messageSteps.length})
                          <ChevronDown
                            size={12}
                            className="transition-transform"
                            style={{
                              transform: showSteps
                                ? "rotate(180deg)"
                                : "rotate(0deg)",
                            }}
                          />
                        </button>
                      )}

                      {/* Toggle for the source list */}
                      {message.citations?.length > 0 && (
                        <button
                          onClick={() => toggleCitations(message.id)}
                          className="flex items-center gap-1 transition-colors"
                          style={{ color: "var(--accent-hover)" }}
                        >
                          <FileText size={12} />
                          {message.citations.length} source
                          {message.citations.length > 1 ? "s" : ""}
                          <ChevronDown
                            size={12}
                            // Rotating the chevron communicates open/closed
                            // state without needing a second icon.
                            className="transition-transform"
                            style={{
                              transform: showSources
                                ? "rotate(180deg)"
                                : "rotate(0deg)",
                            }}
                          />
                        </button>
                      )}
                    </div>

                    {/* ---- Expandable research steps ----
                        Rendered ABOVE the citations: the steps are how the
                        agent found its sources, so they read in that order. */}
                    <AnimatePresence>
                      {showSteps && (
                        <motion.ol
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: "auto" }}
                          exit={{ opacity: 0, height: 0 }}
                          className="glass mt-2 space-y-2 overflow-hidden p-3 text-xs"
                          aria-label="Research steps"
                        >
                          {messageSteps.map((step, index) => (
                            // Index in the key because the same tool can be
                            // called twice with identical arguments.
                            <li key={`${index}-${step.tool}`} className="flex gap-2">
                              <span
                                className="shrink-0 font-medium"
                                style={{ color: "var(--text-muted)" }}
                              >
                                {index + 1}.
                              </span>
                              <div className="min-w-0">
                                <code
                                  className="font-mono font-medium"
                                  style={{ color: "var(--accent-hover)" }}
                                >
                                  {step.tool}
                                </code>
                                {Object.keys(step.args ?? {}).length > 0 && (
                                  <span
                                    className="ml-1.5 wrap-break-word"
                                    style={{ color: "var(--text-muted)" }}
                                  >
                                    ({formatArgs(step.args)})
                                  </span>
                                )}
                                <p style={{ color: "var(--text-secondary)" }}>
                                  {step.summary}
                                </p>
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
                            <div
                              key={citation.chunk_id}
                              className="glass p-3 text-xs"
                            >
                              <div className="mb-1.5 flex items-center justify-between gap-2">
                                <span
                                  className="font-medium"
                                  style={{ color: "var(--accent-hover)" }}
                                >
                                  [{citation.number}] {citation.filename}
                                  {citation.page_number
                                    ? ` · page ${citation.page_number}`
                                    : ""}
                                </span>
                                {/* The similarity score, shown as a percentage
                                    so the user can judge match strength. */}
                                <span className="pill-info shrink-0">
                                  {Math.round(citation.score * 100)}% match
                                </span>
                              </div>
                              <p
                                className="leading-relaxed"
                                style={{ color: "var(--text-secondary)" }}
                              >
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

        {/* ---- "Thinking" indicator ---- */}
        {sending && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex gap-3"
          >
            <div
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg animate-pulse-glow"
              style={{
                background: "rgba(99,102,241,0.16)",
                color: "var(--accent-hover)",
              }}
            >
              <Bot size={15} />
            </div>
            <div
              className="flex items-center gap-1.5 rounded-2xl px-4 py-4"
              style={{ background: "rgba(255,255,255,0.045)" }}
            >
              {/* Three dots with staggered CSS animation delays, producing a
                  travelling wave. Defined in globals.css. */}
              <span
                className="typing-dot h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--accent-hover)" }}
              />
              <span
                className="typing-dot h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--accent-hover)" }}
              />
              <span
                className="typing-dot h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--accent-hover)" }}
              />
              {/* Deep research can take several times longer than a quick
                  answer. Saying so stops a long wait reading as a hang. */}
              {mode === "agent" && (
                <span
                  className="ml-2 text-xs"
                  style={{ color: "var(--text-muted)" }}
                >
                  Researching your documents...
                </span>
              )}
            </div>
          </motion.div>
        )}

        {/* Invisible scroll anchor. Scrolling this into view scrolls the list
            to the bottom. */}
        <div ref={bottomRef} />
      </div>

      {/* ============================================================
          COMPOSER
          ============================================================ */}
      {error && (
        <div
          className="mt-3 rounded-xl px-4 py-2.5 text-sm"
          style={{ background: "rgba(248,113,113,0.1)", color: "var(--danger)" }}
        >
          {error}
        </div>
      )}

      {/* ---- Answer mode toggle ----
          A segmented control: two buttons acting as one radio group. The
          ARIA roles let a screen reader announce it as "Answer mode, Quick
          answer, selected" rather than as two unrelated buttons.

          A radio group comes with a keyboard CONTRACT, so we implement it:
            - ONE Tab stop: only the selected option has tabIndex 0 ("roving
              tabindex"); Tab moves on to the textarea, not to the other option.
            - Arrow keys move the selection AND the focus to the other option.
          The hint underneath is linked with aria-describedby, so a screen
          reader user also hears that Deep research is slower. */}
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
                // Locked while a question is in flight, so the "Researching"
                // indicator always matches the request actually running.
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
        {/* One line on the tradeoff, so the choice is an informed one. */}
        <p id="chat-mode-hint" className="text-xs" style={{ color: "var(--text-muted)" }}>
          {MODES.find((m) => m.value === mode)?.hint}
        </p>
      </div>

      <div className="mt-2 flex items-end gap-2">
        <textarea
          className="input resize-none py-3"
          // Grows from one line to three as the user types a longer question.
          rows={1}
          placeholder="Ask a question about these documents..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter inserts a newline. This is the
            // convention every chat app uses, so it needs no explanation.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          disabled={!sessionId || sending}
        />
        <button
          onClick={() => send()}
          disabled={!input.trim() || sending || !sessionId}
          className="btn-primary h-[46px] w-[46px] shrink-0 px-0!"
          title="Send"
        >
          <ArrowUp size={18} />
        </button>
      </div>
    </div>
  );
}
