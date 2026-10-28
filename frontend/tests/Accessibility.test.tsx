// Runs axe-core (via vitest-axe) over the three signed-in pages -- dashboard, project...

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { configureAxe } from "vitest-axe";

import DashboardPage from "@/app/dashboard/page";
import ProjectPage from "@/app/project/[id]/page";
import SettingsPage from "@/app/settings/page";

const axe = configureAxe({ rules: { "color-contrast": { enabled: false } } });

const mocks = vi.hoisted(() => ({
  getMe: vi.fn(),
  listProjects: vi.fn(),
  listDocuments: vi.fn(),
  health: vi.fn(),
  listMembers: vi.fn(),
  listInvites: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...mocks } };
});

const ADMIN = {
  id: "u1",
  email: "ada@example.com",
  role: "Admin",
  org_id: "o",
  is_active: true,
  is_verified: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function params(id: string) {
  const p = Promise.resolve({ id }) as Promise<{ id: string }> & { status?: string; value?: unknown };
  p.status = "fulfilled";
  p.value = { id };
  return p;
}

async function expectNoViolations(container: Element) {
  container.querySelectorAll<HTMLElement>(".hidden").forEach((el) => {
    el.style.display = "none";
  });
  const results = await axe(container);
  const summary = results.violations.map(
    (v) => `${v.id}: ${v.help}\n  ${v.nodes.map((n) => n.html).join("\n  ")}`
  );
  expect(summary).toEqual([]);
}

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
  localStorage.setItem("edia_token", "a-token");
  mocks.getMe.mockResolvedValue(ADMIN);
  mocks.health.mockResolvedValue(null);
  mocks.listProjects.mockResolvedValue([]);
  mocks.listDocuments.mockResolvedValue([]);
  mocks.listMembers.mockResolvedValue([
    ADMIN,
    { ...ADMIN, id: "m-2", email: "bob@example.com", role: "Employee" },
  ]);
  mocks.listInvites.mockResolvedValue([]);
});

describe("dashboard", () => {
  it("first run (empty state + tour) has no violations", async () => {
    const { container } = render(<DashboardPage />);
    await screen.findByText("No projects yet");
    await screen.findByRole("region", { name: /Projects keep answers on topic/ });
    await expectNoViolations(container);
  });

  it("populated grid has no violations", async () => {
    mocks.listProjects.mockResolvedValue([
      { id: "p1", name: "HR Policies", created_at: "2026-01-01T00:00:00Z" },
      { id: "p2", name: "Contracts", created_at: "2026-02-01T00:00:00Z" },
    ]);
    const { container } = render(<DashboardPage />);
    await screen.findByText("HR Policies");
    await expectNoViolations(container);
  });
});

describe("project", () => {
  it("first run has no violations", async () => {
    mocks.listProjects.mockResolvedValue([{ id: "p1", name: "HR", created_at: "" }]);
    const { container } = render(<ProjectPage params={params("p1")} />);
    await screen.findByText("No documents yet");
    await expectNoViolations(container);
  });

  it("document list has no violations", async () => {
    mocks.listDocuments.mockResolvedValue([
      {
        id: "d1",
        filename: "handbook.pdf",
        file_type: "pdf",
        file_size: 2048,
        status: "completed",
        meta_data: { chunk_count: 3 },
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    const { container } = render(<ProjectPage params={params("p1")} />);
    await screen.findByText("handbook.pdf");
    await expectNoViolations(container);
  });
});

describe("settings", () => {
  it("admin view with the members tab has no violations", async () => {
    const { container } = render(<SettingsPage />);
    await screen.findByRole("button", { name: "Remove bob@example.com" });
    await expectNoViolations(container);
  });
});
