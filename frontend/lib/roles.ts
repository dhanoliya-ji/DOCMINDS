/**
 * roles.ts
 * ========
 * WHAT THIS FILE DOES
 * -------------------
 * Answers "may the signed-in user do X?" for the screens, so a button the
 * backend would refuse is hidden or disabled instead of offered and then
 * failing with a 403.
 *
 * THE BACKEND IS STILL THE AUTHORITY
 * ----------------------------------
 * Nothing here is security. Anyone can edit JavaScript in their own browser,
 * so every one of these rules is enforced again server-side by
 * `deps.require_editor` / `deps.require_admin` (backend/app/core/roles.py).
 * This file only exists so the interface does not offer what will be refused.
 *
 * THE NAMES
 * ---------
 * Roles are STORED as Admin / Manager / Employee and MEAN admin / editor /
 * viewer. The backend accepts both spellings, so this file does too.
 *
 *     viewer  (Employee)  read, search, chat
 *     editor  (Manager)   + upload documents, create projects
 *     admin   (Admin)     + members, invites, API keys, audit log, usage
 */

import type { RoleInput } from "@/lib/api";

const RANK: Record<string, number> = {
  employee: 1,
  viewer: 1,
  manager: 2,
  editor: 2,
  admin: 3,
};

/** 0 for an unknown or missing role, so it can never pass a check. */
function rank(role: string | null | undefined): number {
  return role ? RANK[role.toLowerCase()] ?? 0 : 0;
}

/** Upload documents and create projects. */
export function canEdit(role: string | null | undefined): boolean {
  return rank(role) >= RANK.editor;
}

/** Everything under /settings: members, invites, API keys, audit log, usage. */
export function isAdmin(role: string | null | undefined): boolean {
  return rank(role) >= RANK.admin;
}

/** The friendly name shown in the interface, whichever spelling arrives. */
export function roleLabel(role: string | null | undefined): string {
  switch (rank(role)) {
    case 3:
      return "Admin";
    case 2:
      return "Editor";
    case 1:
      return "Viewer";
    default:
      return "Unknown";
  }
}

/** The choices offered when inviting someone or changing their role. */
export const ROLE_OPTIONS: { value: RoleInput; label: string; help: string }[] = [
  { value: "viewer", label: "Viewer", help: "Search and ask questions" },
  { value: "editor", label: "Editor", help: "Also upload documents and create projects" },
  { value: "admin", label: "Admin", help: "Also manage members, API keys and the audit log" },
];

/** The alias for a stored role, for a <select> whose options use aliases. */
export function roleAlias(role: string | null | undefined): RoleInput {
  switch (rank(role)) {
    case 3:
      return "admin";
    case 2:
      return "editor";
    default:
      return "viewer";
  }
}
