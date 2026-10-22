/**
 * export.ts
 * =========
 * Copy and export answers and whole conversations as Markdown.
 *
 * WHY MARKDOWN
 * ------------
 * It is plain text that renders nicely almost everywhere people paste things
 * -- GitHub, Notion, Confluence, Slack, Obsidian -- and it is what the model
 * already writes its answers in, so lists and bold text survive untouched.
 *
 * The backend has the same layout in backend/app/services/export.py (for API
 * clients, at GET /chat/sessions/{id}/export). This copy exists so the
 * browser can export instantly, including an answer that is still on screen
 * and has not been reloaded from the server.
 */

import type { ChatMessageItem, CitationItem } from "@/lib/api";

const MODE_LABELS: Record<string, string> = { rag: "Quick answer", agent: "Deep research" };

/** A snippet on one line, shortened, safe inside double quotes. */
function quote(text: string, limit = 200): string {
  let flat = (text || "").split(/\s+/).filter(Boolean).join(" ");
  if (flat.length > limit) flat = `${flat.slice(0, limit).trimEnd()}...`;
  return flat.replace(/"/g, "'");
}

/** `1. handbook.pdf, page 12 -- "snippet..."` */
export function citationLine(citation: CitationItem): string {
  let where = citation.filename || "unknown file";
  if (citation.page_number) where += `, page ${citation.page_number}`;
  const snippet = quote(citation.snippet || "");
  return `${citation.number}. ${where}${snippet ? ` -- "${snippet}"` : ""}`;
}

/** One assistant answer with its sources -- what the Copy button copies. */
export function answerToMarkdown(message: ChatMessageItem): string {
  const parts = [message.content.trim()];
  if (message.citations?.length) {
    parts.push("", "**Sources**", "", ...message.citations.map(citationLine));
  }
  return parts.join("\n");
}

/** One message as a Markdown section. */
export function messageToMarkdown(message: ChatMessageItem): string {
  if (message.role === "user") return `## Question\n\n${message.content.trim()}\n`;
  const details = [message.mode ? MODE_LABELS[message.mode] ?? message.mode : null, message.model]
    .filter(Boolean)
    .join(" · ");
  return `## Answer${details ? `  (${details})` : ""}\n\n${answerToMarkdown(message)}\n`;
}

/** A whole conversation, oldest message first. */
export function conversationToMarkdown(
  title: string,
  messages: ChatMessageItem[],
  exportedAt: Date = new Date()
): string {
  const stamp = exportedAt.toISOString().slice(0, 16).replace("T", " ");
  const header = `# ${(title || "Conversation").trim()}\n\n_Exported from DocMinds on ${stamp} UTC_\n\n`;
  return `${header}${messages.map(messageToMarkdown).join("\n").trimEnd()}\n`;
}

/** A download name from a title: letters, digits and dashes only. */
export function safeFilename(title: string, extension = "md"): string {
  const stem =
    (title || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "conversation";
  return `${stem}.${extension}`;
}

/**
 * Save text as a file. A Blob URL on a temporary <a download> is the
 * standard way: no server round trip, and the browser's own save dialog.
 */
export function downloadText(filename: string, text: string, type = "text/markdown"): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked on the next tick: revoking synchronously can cancel the download
  // in some browsers before it has started.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Copy text to the clipboard; resolves false where that is not allowed. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
