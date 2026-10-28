// The conversation history for one project: start a new chat, resume an old one, rename...

"use client";

import { Check, MessageSquarePlus, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";

import type { ChatSessionItem } from "@/lib/api";

interface Props {
  sessions: ChatSessionItem[];
  activeId: string | null;
  loading?: boolean;
  disabled?: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

export default function ChatSidebar({
  sessions,
  activeId,
  loading,
  disabled,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);

  function startRename(session: ChatSessionItem) {
    setConfirming(null);
    setEditing(session.id);
    setDraft(session.title);
  }

  function saveRename() {
    const title = draft.trim();
    if (editing && title) onRename(editing, title);
    setEditing(null);
  }

  return (
    <nav aria-label="Conversation history" className="flex h-full flex-col gap-2">
      <button type="button" className="btn-primary w-full text-xs" onClick={onNew} disabled={disabled}>
        <MessageSquarePlus size={14} aria-hidden="true" />
        New chat
      </button>

      <div className="flex-1 space-y-1 overflow-y-auto">
        {loading && <div className="glass shimmer h-10" aria-busy="true" />}
        {!loading && sessions.length === 0 && (
          <p className="px-2 py-3 text-xs" style={{ color: "var(--text-muted)" }}>
            No conversations yet. Ask a question to start one.
          </p>
        )}
        <ul className="space-y-1">
          {sessions.map((session) => {
            const active = session.id === activeId;
            if (editing === session.id) {
              return (
                <li key={session.id} className="flex items-center gap-1">
                  <input
                    className="input py-1.5! text-xs"
                    value={draft}
                    autoFocus
                    aria-label="Conversation name"
                    maxLength={255}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") saveRename();
                      if (e.key === "Escape") setEditing(null);
                    }}
                  />
                  <button type="button" onClick={saveRename} aria-label="Save name" className="p-1">
                    <Check size={13} />
                  </button>
                  <button type="button" onClick={() => setEditing(null)} aria-label="Cancel rename" className="p-1">
                    <X size={13} />
                  </button>
                </li>
              );
            }
            return (
              <li
                key={session.id}
                className="group flex items-center gap-1 rounded-lg"
                style={{ background: active ? "rgba(99,102,241,0.14)" : "transparent" }}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate px-2 py-2 text-left text-xs"
                  style={{ color: active ? "var(--text-primary)" : "var(--text-secondary)" }}
                  aria-current={active ? "true" : undefined}
                  onClick={() => onSelect(session.id)}
                  disabled={disabled}
                  title={session.title}
                >
                  {session.title}
                </button>
                {confirming === session.id ? (
                  <>
                    <button
                      type="button"
                      className="rounded px-1.5 py-1 text-[11px] font-medium"
                      style={{ color: "var(--danger)" }}
                      onClick={() => {
                        setConfirming(null);
                        onDelete(session.id);
                      }}
                    >
                      Delete?
                    </button>
                    <button type="button" className="p-1" onClick={() => setConfirming(null)} aria-label="Keep conversation">
                      <X size={12} />
                    </button>
                  </>
                ) : (
                  <span className="flex opacity-60 group-hover:opacity-100">
                    <button
                      type="button"
                      className="p-1"
                      onClick={() => startRename(session)}
                      aria-label={`Rename ${session.title}`}
                      disabled={disabled}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      type="button"
                      className="p-1"
                      onClick={() => setConfirming(session.id)}
                      aria-label={`Delete ${session.title}`}
                      disabled={disabled}
                    >
                      <Trash2 size={12} />
                    </button>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}
