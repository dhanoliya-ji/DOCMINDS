// The "Prompts" menu beside the chat box: pick a saved prompt to drop it into the input...

"use client";

import { BookMarked, Trash2, X } from "lucide-react";
import { useState } from "react";

import { api, getErrorMessage, type PromptScope, type PromptTemplate } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Props {
  draft: string;
  canShare: boolean;
  disabled?: boolean;
  onInsert: (body: string) => void;
}

export default function PromptLibrary({ draft, canShare, disabled, onInsert }: Props) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const [prompts, setPrompts] = useState<PromptTemplate[] | null>(null);
  const [error, setError] = useState("");
  const [title, setTitle] = useState("");
  const [scope, setScope] = useState<PromptScope>("personal");

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && prompts === null) {
      try {
        setPrompts(await api.listPrompts());
      } catch (err) {
        setError(getErrorMessage(err, "Could not load the prompts."));
        setPrompts([]);
      }
    }
  }

  async function save() {
    if (!title.trim() || !draft.trim()) return;
    try {
      const saved = await api.createPrompt(title.trim(), draft.trim(), scope);
      setPrompts((current) => [...(current ?? []), saved]);
      setTitle("");
    } catch (err) {
      setError(getErrorMessage(err, "Could not save the prompt."));
    }
  }

  async function remove(id: string) {
    try {
      await api.deletePrompt(id);
      setPrompts((current) => (current ?? []).filter((p) => p.id !== id));
    } catch (err) {
      setError(getErrorMessage(err, "Could not delete the prompt."));
    }
  }

  const label = (p: PromptTemplate) =>
    p.scope === "builtin" && p.key ? t(`prompts.builtin.${p.key}.title`) : p.title;
  const groups: { scope: string; heading: string }[] = [
    { scope: "builtin", heading: t("prompts.builtin") },
    { scope: "personal", heading: t("prompts.personal") },
    { scope: "org", heading: t("prompts.org") },
  ];

  return (
    <div className="relative">
      <button
        type="button"
        className="btn-ghost text-xs"
        onClick={toggle}
        disabled={disabled}
        aria-expanded={open}
        aria-controls="prompt-library"
      >
        <BookMarked size={13} aria-hidden="true" /> {t("prompts.button")}
      </button>
      {open && (
        <div
          id="prompt-library"
          role="region"
          aria-label={t("prompts.title")}
          className="glass absolute bottom-10 left-0 z-30 w-80 p-3 text-xs"
          style={{ background: "var(--bg-elevated)" }}
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold">{t("prompts.title")}</span>
            <button type="button" onClick={() => setOpen(false)} aria-label={t("share.close")}>
              <X size={14} />
            </button>
          </div>
          {prompts === null ? (
            <p style={{ color: "var(--text-muted)" }}>{t("prompts.loading")}</p>
          ) : (
            <div className="max-h-64 space-y-3 overflow-y-auto">
              {groups.map(({ scope: groupScope, heading }) => {
                const items = prompts.filter((p) => p.scope === groupScope);
                if (groupScope !== "personal" && items.length === 0) return null;
                return (
                  <section key={groupScope}>
                    <h3 className="mb-1 font-semibold" style={{ color: "var(--text-muted)" }}>
                      {heading}
                    </h3>
                    {items.length === 0 && <p style={{ color: "var(--text-muted)" }}>{t("prompts.empty")}</p>}
                    <ul className="space-y-0.5">
                      {items.map((p) => (
                        <li key={p.id} className="flex items-center gap-1">
                          <button
                            type="button"
                            className="min-w-0 flex-1 truncate rounded px-1.5 py-1 text-left hover:bg-white/5"
                            title={p.body}
                            onClick={() => {
                              onInsert(p.body);
                              setOpen(false);
                            }}
                          >
                            {label(p)}
                          </button>
                          {p.editable && (
                            <button
                              type="button"
                              className="p-1"
                              aria-label={`${t("prompts.delete")} ${label(p)}`}
                              onClick={() => remove(p.id)}
                            >
                              <Trash2 size={12} />
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
          )}

          {draft.trim() && (
            <div className="mt-3 space-y-2 border-t pt-3" style={{ borderColor: "var(--border-subtle)" }}>
              <p className="font-medium">{t("prompts.saveCurrent")}</p>
              <input
                className="input py-1.5 text-xs"
                placeholder={t("prompts.titleLabel")}
                aria-label={t("prompts.titleLabel")}
                maxLength={120}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <div className="flex items-center gap-2">
                <select
                  className="input flex-1 py-1.5 text-xs"
                  aria-label={t("prompts.scopeLabel")}
                  value={scope}
                  onChange={(e) => setScope(e.target.value as PromptScope)}
                >
                  <option value="personal">{t("prompts.personal")}</option>
                  {canShare && <option value="org">{t("prompts.org")}</option>}
                </select>
                <button type="button" className="btn-primary text-xs" onClick={save} disabled={!title.trim()}>
                  {t("prompts.save")}
                </button>
              </div>
            </div>
          )}
          {error && (
            <p role="alert" className="mt-2" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
