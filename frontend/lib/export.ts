// Copy and export answers and whole conversations as Markdown.

import type { ChatMessageItem, CitationItem } from "@/lib/api";

const MODE_LABELS: Record<string, string> = { rag: "Quick answer", agent: "Deep research" };

function quote(text: string, limit = 200): string {
  let flat = (text || "").split(/\s+/).filter(Boolean).join(" ");
  if (flat.length > limit) flat = `${flat.slice(0, limit).trimEnd()}...`;
  return flat.replace(/"/g, "'");
}

export function citationLine(citation: CitationItem): string {
  let where = citation.filename || "unknown file";
  if (citation.page_number) where += `, page ${citation.page_number}`;
  const snippet = quote(citation.snippet || "");
  return `${citation.number}. ${where}${snippet ? ` -- "${snippet}"` : ""}`;
}

export function answerToMarkdown(message: ChatMessageItem): string {
  const parts = [message.content.trim()];
  if (message.citations?.length) {
    parts.push("", "**Sources**", "", ...message.citations.map(citationLine));
  }
  return parts.join("\n");
}

export function messageToMarkdown(message: ChatMessageItem): string {
  if (message.role === "user") return `## Question\n\n${message.content.trim()}\n`;
  const details = [message.mode ? MODE_LABELS[message.mode] ?? message.mode : null, message.model]
    .filter(Boolean)
    .join(" · ");
  return `## Answer${details ? `  (${details})` : ""}\n\n${answerToMarkdown(message)}\n`;
}

export function conversationToMarkdown(
  title: string,
  messages: ChatMessageItem[],
  exportedAt: Date = new Date()
): string {
  const stamp = exportedAt.toISOString().slice(0, 16).replace("T", " ");
  const header = `# ${(title || "Conversation").trim()}\n\n_Exported from DocMinds on ${stamp} UTC_\n\n`;
  return `${header}${messages.map(messageToMarkdown).join("\n").trimEnd()}\n`;
}

export function safeFilename(title: string, extension = "md"): string {
  const stem =
    (title || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "conversation";
  return `${stem}.${extension}`;
}

export function downloadText(filename: string, text: string, type = "text/markdown"): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
