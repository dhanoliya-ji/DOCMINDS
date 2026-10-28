// A project tile for the dashboard grid.

"use client";

import { ArrowRight, FolderOpen } from "lucide-react";
import Link from "next/link";

import type { Project } from "@/lib/api";
import { useT } from "@/lib/i18n";

export default function ProjectCard({ project, index = 0 }: { project: Project; index?: number }) {
  const { t, locale } = useT();
  const created = new Date(project.created_at).toLocaleDateString(locale === "hi" ? "hi-IN" : undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <Link
      href={`/project/${project.id}`}
      className="glass lift-card block min-w-0 p-5 text-left"
      data-testid="project-card"
    >
      <div
        className="blur-reveal mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl"
        style={{
          background: "linear-gradient(135deg, color-mix(in srgb, var(--accent) 22%, transparent), color-mix(in srgb, var(--cyan) 18%, transparent))",
          color: "var(--accent-hover)",
          ["--reveal-delay" as string]: `${Math.min(index, 8) * 60}ms`,
        }}
        aria-hidden="true"
      >
        <FolderOpen size={18} />
      </div>
      <h2 className="truncate font-semibold">{project.name}</h2>
      <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
        {t("projectCard.created", { date: created })}
      </p>

      <div className="card-preview">
        <div>
          <p
            className="mt-3 flex items-center gap-1.5 border-t pt-3 text-xs font-medium"
            style={{ borderColor: "var(--border-subtle)", color: "var(--accent-hover)" }}
          >
            {t("projectCard.open")}
            <ArrowRight size={13} aria-hidden="true" />
          </p>
        </div>
      </div>
    </Link>
  );
}
