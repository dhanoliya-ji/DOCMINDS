// Answers "may the signed-in user do X?" for the screens, so a button the backend would...

import type { RoleInput } from "@/lib/api";

const RANK: Record<string, number> = {
  employee: 1,
  viewer: 1,
  manager: 2,
  editor: 2,
  admin: 3,
};

function rank(role: string | null | undefined): number {
  return role ? RANK[role.toLowerCase()] ?? 0 : 0;
}

export function canEdit(role: string | null | undefined): boolean {
  return rank(role) >= RANK.editor;
}

export function isAdmin(role: string | null | undefined): boolean {
  return rank(role) >= RANK.admin;
}

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

export const ROLE_OPTIONS: { value: RoleInput; label: string; help: string }[] = [
  { value: "viewer", label: "Viewer", help: "Search and ask questions" },
  { value: "editor", label: "Editor", help: "Also upload documents and create projects" },
  { value: "admin", label: "Admin", help: "Also manage members, API keys and the audit log" },
];

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
