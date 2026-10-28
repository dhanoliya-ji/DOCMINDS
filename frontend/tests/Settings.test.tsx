// The /settings admin area and the dashboard's role-aware bits.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/dashboard/page";
import SettingsPage from "@/app/settings/page";
import { AUDIT_PAGE_SIZE } from "@/components/settings/AuditLogPanel";
import { ApiError } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  getMe: vi.fn(),
  listMembers: vi.fn(),
  listInvites: vi.fn(),
  createInvite: vi.fn(),
  updateMember: vi.fn(),
  removeMember: vi.fn(),
  revokeInvite: vi.fn(),
  listApiKeys: vi.fn(),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
  listAuditLogs: vi.fn(),
  getUsage: vi.fn(),
  getFeedbackSummary: vi.fn(),
  resendVerification: vi.fn(),
  listProjects: vi.fn(),
  health: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...mocks } };
});

const ADMIN = {
  id: "admin-1",
  email: "ada@example.com",
  role: "Admin",
  org_id: "o",
  is_active: true,
  is_verified: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const MEMBERS = [
  { ...ADMIN },
  { id: "m-2", email: "bob@example.com", role: "Employee", is_active: true, is_verified: false, created_at: "" },
];

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
  localStorage.setItem("edia_token", "a-token");
  mocks.getMe.mockResolvedValue(ADMIN);
  mocks.listMembers.mockResolvedValue(MEMBERS);
  mocks.listInvites.mockResolvedValue([]);
  mocks.listApiKeys.mockResolvedValue([]);
  mocks.listProjects.mockResolvedValue([]);
  mocks.health.mockResolvedValue(null);
});

async function hold(button: HTMLElement) {
  fireEvent.pointerDown(button);
  await act(() => new Promise((r) => setTimeout(r, 1100)));
  fireEvent.pointerUp(button);
}


describe("settings access", () => {
  it("shows a non-admin their account and no admin tabs", async () => {
    mocks.getMe.mockResolvedValue({ ...ADMIN, role: "Manager" });
    render(<SettingsPage />);

    expect(await screen.findByText(/managed by your/)).toBeInTheDocument();
    expect(screen.getByText(/Editor/)).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(mocks.listMembers).not.toHaveBeenCalled();
  });

  it("gives an admin five accessible tabs, Members first", async () => {
    render(<SettingsPage />);

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Members", "API keys", "Audit log", "Usage", "Feedback"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "tab-members");
  });

  it("moves between tabs with the arrow keys", async () => {
    mocks.listApiKeys.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<SettingsPage />);

    const first = (await screen.findAllByRole("tab"))[0];
    first.focus();
    await user.keyboard("{ArrowRight}");

    expect(screen.getByRole("tab", { name: "API keys" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "API keys" })).toHaveFocus();
  });
});


describe("members", () => {
  it("lists members and offers no controls on your own row", async () => {
    render(<SettingsPage />);

    await screen.findByText("bob@example.com");
    expect(screen.getByRole("combobox", { name: "Role for bob@example.com" })).toHaveValue("viewer");
    expect(screen.queryByRole("combobox", { name: /Role for ada/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove ada/ })).not.toBeInTheDocument();
    expect(screen.getByText("Email not confirmed")).toBeInTheDocument();
  });

  it("invites by email and role, and shows the link once", async () => {
    mocks.createInvite.mockResolvedValue({
      id: "i1",
      email: "carol@example.com",
      role: "Manager",
      status: "pending",
      invite_url: "http://localhost:3000/login?invite=tok",
      token: "tok",
    });
    const user = userEvent.setup();
    render(<SettingsPage />);

    await user.type(await screen.findByPlaceholderText("colleague@company.com"), "carol@example.com");
    await user.selectOptions(screen.getByRole("combobox", { name: "Role" }), "editor");
    await user.click(screen.getByRole("button", { name: /Send invite/ }));

    expect(mocks.createInvite).toHaveBeenCalledWith("carol@example.com", "editor");
    expect(await screen.findByText("http://localhost:3000/login?invite=tok")).toBeInTheDocument();
  });

  it("changes a member's role", async () => {
    mocks.updateMember.mockResolvedValue({});
    const user = userEvent.setup();
    render(<SettingsPage />);

    await user.selectOptions(await screen.findByRole("combobox", { name: "Role for bob@example.com" }), "editor");

    expect(mocks.updateMember).toHaveBeenCalledWith("m-2", { role: "editor" });
  });

  it("removes a member only after a completed hold", async () => {
    mocks.removeMember.mockResolvedValue({});
    const user = userEvent.setup();
    render(<SettingsPage />);

    const remove = await screen.findByRole("button", { name: "Remove bob@example.com" });
    await user.click(remove);
    expect(mocks.removeMember).not.toHaveBeenCalled();

    await hold(remove);
    await waitFor(() => expect(mocks.removeMember).toHaveBeenCalledWith("m-2"));
  });

  it("shows the backend's refusal", async () => {
    mocks.updateMember.mockRejectedValue(new ApiError("An organization must keep at least one admin.", 400));
    const user = userEvent.setup();
    render(<SettingsPage />);

    await user.selectOptions(await screen.findByRole("combobox", { name: "Role for bob@example.com" }), "admin");

    expect(await screen.findByRole("alert")).toHaveTextContent(/at least one admin/);
  });
});


describe("API keys", () => {
  async function openKeys() {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(await screen.findByRole("tab", { name: "API keys" }));
    return user;
  }

  it("shows a new key once, with a warning", async () => {
    mocks.createApiKey.mockResolvedValue({ id: "k1", name: "CI", key: "dm_secret_value", role: "Employee" });
    const user = await openKeys();

    await user.type(screen.getByPlaceholderText(/Nightly report/), "CI");
    await user.type(screen.getByPlaceholderText("Never"), "30");
    await user.click(screen.getByRole("button", { name: /Create key/ }));

    expect(mocks.createApiKey).toHaveBeenCalledWith("CI", "viewer", 30);
    expect(await screen.findByTestId("new-api-key")).toHaveTextContent("dm_secret_value");
    expect(screen.getByRole("status")).toHaveTextContent(/will not be shown again/);
  });

  it("lists keys by prefix and revokes after confirming", async () => {
    mocks.listApiKeys.mockResolvedValue([
      { id: "k1", name: "CI", key_prefix: "dm_ab12", role: "Employee", is_active: true, revoked_at: null, last_used_at: null },
      { id: "k2", name: "Old", key_prefix: "dm_cd34", role: "Employee", is_active: false, revoked_at: "2026-01-01", last_used_at: null },
    ]);
    mocks.revokeApiKey.mockResolvedValue({});
    await openKeys();

    expect(await screen.findByText("dm_ab12…")).toBeInTheDocument();
    expect(screen.getByText("Revoked")).toBeInTheDocument();
    await hold(screen.getByRole("button", { name: "Revoke CI" }));

    await waitFor(() => expect(mocks.revokeApiKey).toHaveBeenCalledWith("k1"));
  });
});


describe("audit log", () => {
  function pageOf(offset: number, total: number) {
    const count = Math.min(AUDIT_PAGE_SIZE, total - offset);
    return {
      items: Array.from({ length: count }, (_, i) => ({
        id: `e${offset + i}`,
        action: "user.login",
        details: {},
        ip_address: "203.0.113.9",
        user_id: "admin-1-uuid",
        created_at: "2026-10-01T10:00:00Z",
      })),
      total,
      limit: AUDIT_PAGE_SIZE,
      offset,
    };
  }

  it("pages through the log with limit and offset", async () => {
    mocks.listAuditLogs.mockImplementation(async ({ offset }) => pageOf(offset, 30));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(await screen.findByRole("tab", { name: "Audit log" }));

    expect(await screen.findByText(`1–${AUDIT_PAGE_SIZE} of 30`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Previous/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Next/ }));

    expect(await screen.findByText(`${AUDIT_PAGE_SIZE + 1}–30 of 30`)).toBeInTheDocument();
    expect(mocks.listAuditLogs).toHaveBeenLastCalledWith({
      limit: AUDIT_PAGE_SIZE,
      offset: AUDIT_PAGE_SIZE,
      action: undefined,
    });
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
  });

  it("filters by action from the first page", async () => {
    mocks.listAuditLogs.mockResolvedValue(pageOf(0, 1));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(await screen.findByRole("tab", { name: "Audit log" }));

    await user.type(await screen.findByRole("textbox", { name: "Filter by action" }), "member.");
    await user.click(screen.getByRole("button", { name: "Filter" }));

    await waitFor(() =>
      expect(mocks.listAuditLogs).toHaveBeenLastCalledWith({ limit: AUDIT_PAGE_SIZE, offset: 0, action: "member." })
    );
  });
});


describe("usage", () => {
  it("shows tokens against the quota as a progressbar", async () => {
    mocks.getUsage.mockResolvedValue({
      period_start: "2026-10-01T00:00:00Z",
      period_end: "2026-11-01T00:00:00Z",
      requests: 12,
      prompt_tokens: 600_000,
      completion_tokens: 250_000,
      estimated_cost_usd: 0.24,
      tokens: { used: 850_000, quota: 1_000_000, unlimited: false, remaining: 150_000 },
      documents: { used: 3, quota: 0, unlimited: true, remaining: null },
      storage_bytes: { used: 1024, quota: 5368709120, unlimited: false, remaining: 1 },
      by_model: [],
      by_mode: [],
    });
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(await screen.findByRole("tab", { name: "Usage" }));

    const bar = await screen.findByRole("progressbar", { name: "AI tokens this month" });
    expect(bar).toHaveAttribute("aria-valuenow", "85");
    expect(screen.getByText("$0.2400")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Documents" })).not.toBeInTheDocument();
    expect(screen.getByText(/no limit/)).toBeInTheDocument();
  });
});


describe("feedback tab", () => {
  const SUMMARY = {
    total: 4,
    up: 3,
    down: 1,
    satisfaction: 0.75,
    by_mode: [{ key: "rag", up: 2, down: 1 }, { key: "agent", up: 1, down: 0 }],
    by_model: [{ key: "gpt-oss", up: 3, down: 1 }],
    recent_negative: [
      {
        message_id: "m1", session_id: "s1", rating: -1, comment: "wrong page",
        question: "How much leave do contractors get?", answer: "Contractors get 25 days.",
        mode: "rag", model: "gpt-oss", created_at: "2026-10-20T10:00:00Z",
      },
    ],
  };

  it("summarises satisfaction and lists the unhelpful answers", async () => {
    mocks.getMe.mockResolvedValue(ADMIN);
    mocks.getFeedbackSummary.mockResolvedValue(SUMMARY);
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(await screen.findByRole("tab", { name: "Feedback" }));

    expect(await screen.findByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Deep research")).toBeInTheDocument();
    expect(screen.getByText(/How much leave do contractors get\?/)).toBeInTheDocument();
    expect(screen.getByText(/wrong page/)).toBeInTheDocument();
    expect(mocks.getFeedbackSummary).toHaveBeenCalledWith(30);
  });

  it("changes the period", async () => {
    mocks.getMe.mockResolvedValue(ADMIN);
    mocks.getFeedbackSummary.mockResolvedValue(SUMMARY);
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(await screen.findByRole("tab", { name: "Feedback" }));
    await user.click(await screen.findByRole("button", { name: "Last 7 days" }));

    await waitFor(() => expect(mocks.getFeedbackSummary).toHaveBeenCalledWith(7));
  });

  it("says so when nothing has been rated", async () => {
    mocks.getMe.mockResolvedValue(ADMIN);
    mocks.getFeedbackSummary.mockResolvedValue({
      ...SUMMARY, total: 0, up: 0, down: 0, satisfaction: null, by_mode: [], by_model: [], recent_negative: [],
    });
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(await screen.findByRole("tab", { name: "Feedback" }));

    expect(await screen.findByText(/No answers have been rated/)).toBeInTheDocument();
  });
});


describe("dashboard", () => {
  it("offers New project and Settings to an editor or admin", async () => {
    render(<DashboardPage />);
    expect(await screen.findByRole("button", { name: /New project/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("does not offer New project to a viewer", async () => {
    mocks.getMe.mockResolvedValue({ ...ADMIN, role: "Employee" });
    render(<DashboardPage />);

    expect(await screen.findByText(/Ask an editor or admin/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /New project|Create a project/ })).not.toBeInTheDocument();
  });

  it("asks an unconfirmed user to confirm, and resends the link", async () => {
    mocks.getMe.mockResolvedValue({ ...ADMIN, is_verified: false });
    mocks.resendVerification.mockResolvedValue({ detail: "A new confirmation link is on its way." });
    const user = userEvent.setup();
    render(<DashboardPage />);

    const banner = await screen.findByRole("region", { name: "Email confirmation" });
    await user.click(within(banner).getByRole("button", { name: /Resend link/ }));

    expect(await within(banner).findByRole("status")).toHaveTextContent(/on its way/);
  });

  it("shows no banner once the email is confirmed", async () => {
    render(<DashboardPage />);
    await screen.findByRole("button", { name: /New project/ });
    expect(screen.queryByRole("region", { name: "Email confirmation" })).not.toBeInTheDocument();
  });
});
