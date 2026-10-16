/**
 * ChatPanel.tsx
 * =============
 * The conversation interface — where the whole RAG system is finally used.
 *
 * WHAT HAPPENS WHEN YOU SEND A MESSAGE
 * ------------------------------------
 * The frontend does very little of the work. It makes ONE request:
 *
 *     POST /api/v1/chat/sessions/{id}/messages   { "message": "..." }
 *
 * and the backend performs the entire pipeline before replying: embed the
 * question, search pgvector for the closest chunks, paste them into a prompt,
 * ask the language model to answer using only those excerpts, build the citations, and
 * save both messages.
 *
 * What comes back is the answer plus the list of sources that produced it,
 * which is what this component renders.
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
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  User,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { ChatMessageItem, CitationItem, api, getErrorMessage } from "@/lib/api";

interface Props {
  projectId: string;
}

/** Example questions offered on an empty conversation, to get people started. */
const SUGGESTIONS = [
  "Summarise the key points of these documents",
  "What are the main policies described here?",
  "List any deadlines or dates mentioned",
];

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
      const response = await api.askQuestion(sessionId, question);

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

  /** Expand or collapse the sources under one assistant message. */
  function toggleCitations(messageId: string) {
    setExpanded((current) => {
      // Copy the Set before mutating. React compares by reference, so mutating
      // the existing Set in place would not trigger a re-render.
      const next = new Set(current);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        next.add(messageId);
      }
      return next;
    });
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

      <div className="mt-4 flex items-end gap-2">
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
