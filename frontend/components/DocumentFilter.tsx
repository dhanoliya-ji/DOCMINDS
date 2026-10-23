/**
 * DocumentFilter.tsx
 * ==================
 * "Only search these files." A small multi-select used by both the Search
 * and the Ask AI tabs.
 *
 * Nothing ticked means ALL documents -- the default, and what an empty
 * filter means to the backend too (document_ids omitted). Only documents
 * that finished processing are offered, because only they are searchable.
 */

"use client";

import { Check, ChevronDown, Filter } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { DocumentItem } from "@/lib/api";
import { translate, useT, type Vars } from "@/lib/i18n";

interface Props {
  documents: DocumentItem[];
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}

/** The button's label: "All documents", "handbook.pdf" or "3 documents". */
export function filterLabel(
  documents: DocumentItem[],
  selected: string[],
  // English unless the caller passes the current language's `t`.
  t: (key: string, vars?: Vars) => string = (key, vars) => translate("en", key, vars)
): string {
  const chosen = documents.filter((d) => selected.includes(d.id));
  if (chosen.length === 0) return t("filter.all");
  if (chosen.length === 1) return chosen[0].filename;
  return t("filter.many", { count: chosen.length });
}

export default function DocumentFilter({ documents, selected, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { t } = useT();

  // A click anywhere else closes the list, like a native <select>.
  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
  }

  if (documents.length === 0) return null;

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        className="btn-ghost text-xs"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={t("filter.title")}
      >
        <Filter size={13} aria-hidden="true" />
        <span className="max-w-[12rem] truncate">{filterLabel(documents, selected, t)}</span>
        <ChevronDown size={12} aria-hidden="true" />
      </button>
      {open && (
        <div
          className="glass absolute z-30 mt-1 max-h-72 w-72 overflow-y-auto p-1"
          style={{ background: "var(--bg-elevated)" }}
        >
          <ul role="listbox" aria-multiselectable="true" aria-label={t("filter.listLabel")}>
            <li>
              <button
                type="button"
                role="option"
                aria-selected={selected.length === 0}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-white/5"
                onClick={() => onChange([])}
              >
                <Check size={12} style={{ opacity: selected.length === 0 ? 1 : 0 }} aria-hidden="true" />
                {t("filter.all")}
              </button>
            </li>
            {documents.map((doc) => {
              const active = selected.includes(doc.id);
              return (
                <li key={doc.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-white/5"
                    onClick={() => toggle(doc.id)}
                  >
                    <Check size={12} style={{ opacity: active ? 1 : 0 }} aria-hidden="true" />
                    <span className="truncate">{doc.filename}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
