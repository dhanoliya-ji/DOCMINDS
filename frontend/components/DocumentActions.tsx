// The editor-only controls on one row of the document list: rename, re-index and delete.

"use client";

import { Check, Pencil, RefreshCw, Trash2, X } from "lucide-react";
import { useState } from "react";

import { DocumentItem, api, getErrorMessage } from "@/lib/api";

interface Props {
  document: DocumentItem;
  onChanged: () => void;
  onError: (message: string) => void;
}

export default function DocumentActions({ document, onChanged, onError }: Props) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(document.filename);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const working = document.status === "pending" || document.status === "processing";

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    try {
      await action();
      onChanged();
    } catch (err) {
      onError(getErrorMessage(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    const save = () => {
      setEditing(false);
      const trimmed = name.trim();
      if (trimmed && trimmed !== document.filename) {
        run(() => api.renameDocument(document.id, trimmed), "Could not rename the document.");
      }
    };
    return (
      <div className="flex items-center gap-1">
        <input
          className="input py-1! text-xs"
          value={name}
          autoFocus
          aria-label="Document name"
          maxLength={255}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") setEditing(false);
          }}
        />
        <button type="button" className="p-1" onClick={save} aria-label="Save name">
          <Check size={13} />
        </button>
        <button type="button" className="p-1" onClick={() => setEditing(false)} aria-label="Cancel rename">
          <X size={13} />
        </button>
      </div>
    );
  }

  if (confirming) {
    return (
      <div className="flex items-center gap-1 text-xs">
        <button
          type="button"
          className="rounded px-2 py-1 font-medium"
          style={{ color: "var(--danger)" }}
          disabled={busy}
          onClick={() => {
            setConfirming(false);
            run(() => api.deleteDocument(document.id), "Could not delete the document.");
          }}
        >
          Delete?
        </button>
        <button type="button" className="p-1" onClick={() => setConfirming(false)} aria-label="Keep document">
          <X size={13} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-0.5" style={{ color: "var(--text-muted)" }}>
      <button
        type="button"
        className="rounded p-1.5 hover:bg-white/5"
        onClick={() => {
          setName(document.filename);
          setEditing(true);
        }}
        disabled={busy}
        aria-label={`Rename ${document.filename}`}
        title="Rename"
      >
        <Pencil size={13} />
      </button>
      <button
        type="button"
        className="rounded p-1.5 hover:bg-white/5 disabled:opacity-40"
        onClick={() => run(() => api.reindexDocument(document.id), "Could not re-index the document.")}
        disabled={busy || working}
        aria-label={`Re-index ${document.filename}`}
        title={working ? "Already queued or processing" : "Re-index (process the file again)"}
      >
        <RefreshCw size={13} className={busy ? "animate-spin" : undefined} />
      </button>
      <button
        type="button"
        className="rounded p-1.5 hover:bg-white/5"
        onClick={() => setConfirming(true)}
        disabled={busy}
        aria-label={`Delete ${document.filename}`}
        title="Delete"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}
