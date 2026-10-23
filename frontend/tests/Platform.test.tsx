/**
 * Platform.test.tsx
 * =================
 * The pages for the optional platform features (lib/platform.ts):
 *
 *   * Plan and billing: the current plan, Upgrade -> Stripe Checkout,
 *     Manage billing -> the portal, the return notices, non-admins get no
 *     buttons, "not enabled" on a 404
 *   * Cloud imports: providers, connect -> the consent URL, browse a
 *     connection (open a folder, select, import, per-item results), the
 *     imported documents table and Sync now, "not enabled" on a 404
 *   * Admin dashboard: totals, a chart per series with its data table, top
 *     questions; axe finds nothing
 *   * Settings: the feature links and the plan badge appear only when the
 *     server has each feature on
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { configureAxe } from "vitest-axe";

import AdminDashboardPage from "@/app/settings/admin/page";
import BillingPage from "@/app/settings/billing/page";
import IntegrationsPage from "@/app/settings/integrations/page";
import SettingsPage from "@/app/settings/page";
import { ApiError } from "@/lib/api";
import { searchParamsMock } from "./setup";

const axe = configureAxe({ rules: { "color-contrast": { enabled: false } } });

const apiMocks = vi.hoisted(() => ({
  getMe: vi.fn(),
  listProjects: vi.fn(),
  listMembers: vi.fn(),
  listInvites: vi.fn(),
  listWebhooks: vi.fn(),
}));

const platformMocks = vi.hoisted(() => ({
  listProviders: vi.fn(),
  authorize: vi.fn(),
  listConnections: vi.fn(),
  disconnect: vi.fn(),
  browse: vi.fn(),
  importItems: vi.fn(),
  getImport: vi.fn(),
  listImports: vi.fn(),
  listLinkedDocuments: vi.fn(),
  setAutoSync: vi.fn(),
  syncDocument: vi.fn(),
  syncConnection: vi.fn(),
  getBilling: vi.fn(),
  checkout: vi.fn(),
  openPortal: vi.fn(),
  getAnalytics: vi.fn(),
}));
const navigateTo = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

vi.mock("@/lib/platform", async () => {
  const actual = await vi.importActual<typeof import("@/lib/platform")>("@/lib/platform");
  return {
    ...actual,
    platformApi: platformMocks,
    navigate: { to: navigateTo },
    integrationsAvailable: () => platformMocks.listProviders().then(() => true, () => false),
    analyticsAvailable: () => platformMocks.getAnalytics(1).then(() => true, () => false),
  };
});

const USER = (role: string) => ({
  id: "u1", email: "ada@example.com", role, org_id: "o", is_active: true, is_verified: true,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
});

const LIMITS = { monthly_tokens: 1000000, max_documents: 1000, max_storage_bytes: 5368709120 };
const BILLING = {
  plan: "free",
  subscription_status: null,
  renews_at: null,
  has_customer: false,
  limits: LIMITS,
  plans: [
    { id: "free", price_label: "$0", limits: LIMITS, purchasable: false },
    { id: "pro", price_label: "$29 / month", limits: { ...LIMITS, max_documents: 10000 }, purchasable: true },
    { id: "team", price_label: "$99 / month", limits: { ...LIMITS, max_documents: 0 }, purchasable: true },
  ],
};

const CONNECTION = {
  id: "c1", provider: "google_drive", account_label: "ada@example.com", status: "active",
  last_error: null, created_at: "2026-01-01T00:00:00Z",
};

const LINKED = {
  document_id: "d1", project_id: "p1", connection_id: "c1", provider: "google_drive",
  external_id: "f1", external_name: "Leave policy", filename: "Leave policy.docx",
  document_status: "completed", revision: "3", web_url: null, auto_sync: true,
  sync_status: "synced", last_error: null, last_synced_at: "2026-01-02T00:00:00Z", last_checked_at: null,
};

function day(date: string, extra: Record<string, number>) {
  return { date, ...extra };
}

const OVERVIEW = {
  days: 2,
  since: "2026-10-22T00:00:00Z",
  totals: {
    requests: 12, tokens: 3400, cost_usd: 0.0123, documents_processed: 4, documents_failed: 1,
    active_users: 3, feedback_up: 3, feedback_down: 1, satisfaction: 0.75,
  },
  usage: [day("2026-10-22", { requests: 5, tokens: 1000, cost_usd: 0.004 }), day("2026-10-23", { requests: 7, tokens: 2400, cost_usd: 0.0083 })],
  documents: [day("2026-10-22", { processed: 1, failed: 1 }), day("2026-10-23", { processed: 3, failed: 0 })],
  active_users: [day("2026-10-22", { active_users: 2 }), day("2026-10-23", { active_users: 3 })],
  feedback: [day("2026-10-22", { up: 1, down: 1 }), day("2026-10-23", { up: 2, down: 0 })],
  top_questions: [{ question: "What is the leave policy?", count: 4 }],
};

beforeEach(() => {
  Object.values(apiMocks).forEach((m) => m.mockReset());
  Object.values(platformMocks).forEach((m) => m.mockReset());
  navigateTo.mockReset();
  localStorage.setItem("edia_token", "a-token");
  apiMocks.getMe.mockResolvedValue(USER("Admin"));
  apiMocks.listProjects.mockResolvedValue([{ id: "p1", name: "Handbook" }]);
  apiMocks.listMembers.mockResolvedValue([USER("Admin")]);
  apiMocks.listInvites.mockResolvedValue([]);
  apiMocks.listWebhooks.mockRejectedValue(new ApiError("Not Found", 404));
  platformMocks.getBilling.mockResolvedValue(BILLING);
  platformMocks.listProviders.mockResolvedValue([
    { id: "google_drive", name: "Google Drive", configured: true },
    { id: "notion", name: "Notion", configured: false },
  ]);
  platformMocks.listConnections.mockResolvedValue([CONNECTION]);
  platformMocks.listLinkedDocuments.mockResolvedValue([LINKED]);
  platformMocks.getAnalytics.mockResolvedValue(OVERVIEW);
  platformMocks.listImports.mockResolvedValue([]);
});

describe("Plan and billing", () => {
  it("shows the plan and sends an admin to Stripe Checkout", async () => {
    platformMocks.checkout.mockResolvedValue({ url: "https://checkout.stripe.com/c/1" });
    render(<BillingPage />);
    expect(await screen.findByText("Current plan")).toBeInTheDocument();
    expect(screen.getByText("10,000 documents")).toBeInTheDocument();
    expect(screen.getByText("Unlimited documents")).toBeInTheDocument();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Upgrade to Pro" }));
    expect(platformMocks.checkout).toHaveBeenCalledWith("pro");
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("https://checkout.stripe.com/c/1"));
  });

  it("offers the portal to a paying customer", async () => {
    platformMocks.getBilling.mockResolvedValue({ ...BILLING, plan: "pro", has_customer: true, subscription_status: "active" });
    platformMocks.openPortal.mockResolvedValue({ url: "https://billing.stripe.com/p/1" });
    render(<BillingPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Manage billing" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("https://billing.stripe.com/p/1"));
    expect(screen.getByText("Subscription: active")).toBeInTheDocument();
  });

  it("thanks the customer on return, without changing the plan itself", async () => {
    searchParamsMock.value = new URLSearchParams("checkout=success");
    render(<BillingPage />);
    expect(await screen.findByText(/Your plan updates as soon as Stripe confirms/)).toBeInTheDocument();
  });

  it("gives non-admins no buttons", async () => {
    apiMocks.getMe.mockResolvedValue(USER("Employee"));
    render(<BillingPage />);
    expect(await screen.findByText("Only admins can change the plan.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Upgrade/ })).not.toBeInTheDocument();
  });

  it("says so when billing is off", async () => {
    platformMocks.getBilling.mockRejectedValue(new ApiError("Not Found", 404));
    render(<BillingPage />);
    expect(await screen.findByText("Billing is not enabled on this server.")).toBeInTheDocument();
  });
});

describe("Cloud imports", () => {
  it("connects a provider through its consent URL", async () => {
    platformMocks.authorize.mockResolvedValue({ authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?x" });
    render(<IntegrationsPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Connect Google Drive" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?x"));
    expect(screen.getByRole("button", { name: "Notion (not configured)" })).toBeDisabled();
  });

  it("browses, opens a folder, and imports the selection", async () => {
    const user = userEvent.setup();
    platformMocks.browse
      .mockResolvedValueOnce({
        items: [{ id: "fold", name: "Policies", kind: "folder", importable: false, has_children: false }],
        next_cursor: null,
      })
      .mockResolvedValueOnce({
        items: [{ id: "f1", name: "Leave policy", kind: "file", importable: true, has_children: false }],
        next_cursor: null,
      });
    const job = (status: string, item: Record<string, unknown>) => ({
      id: "j1", connection_id: "c1", project_id: "p1", provider: "google_drive", status,
      auto_sync: true, counts: {}, created_at: null, finished_at: null,
      items: [{ item_id: "f1", name: null, document_id: null, detail: null, ...item }],
    });
    // 202 with every item pending, then the poll sees it downloading, then done.
    platformMocks.importItems.mockResolvedValue(job("queued", { status: "pending" }));
    platformMocks.getImport
      .mockResolvedValueOnce(job("running", { status: "downloading", name: "Leave policy" }))
      .mockResolvedValueOnce(job("completed", { status: "imported", name: "Leave policy", document_id: "d9" }));
    render(<IntegrationsPage />);
    await user.click(await screen.findByRole("button", { name: "Browse" }));
    await user.click(await screen.findByRole("button", { name: "Open Policies" }));
    expect(platformMocks.browse).toHaveBeenLastCalledWith("c1", { parent: "fold", q: undefined, cursor: undefined });
    await user.click(await screen.findByRole("checkbox", { name: "Leave policy" }));
    await user.click(screen.getByRole("button", { name: "Import 1 selected" }));
    expect(platformMocks.importItems).toHaveBeenCalledWith("c1", "p1", ["f1"], true);
    expect(await screen.findByText("Waiting")).toBeInTheDocument();
    expect(await screen.findByText("Downloading...", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(await screen.findByText("Imported", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText("Import finished")).toBeInTheDocument();
    expect(platformMocks.getImport).toHaveBeenCalledWith("j1");
  });

  it("does not bring back an import that already finished", async () => {
    platformMocks.listImports.mockResolvedValue([
      {
        id: "j2", connection_id: "c1", project_id: "p1", provider: "google_drive", status: "completed",
        auto_sync: true, counts: { failed: 1 }, created_at: null, finished_at: null,
        items: [{ item_id: "x", name: "tool.exe", status: "failed", document_id: null, detail: "not supported" }],
      },
    ]);
    render(<IntegrationsPage />);
    await screen.findByRole("button", { name: "Browse" });
    expect(screen.queryByText("tool.exe")).not.toBeInTheDocument();
  });

  it("links to the provider's page when access cannot be revoked by API", async () => {
    platformMocks.listConnections.mockResolvedValue([{ ...CONNECTION, provider: "onedrive" }]);
    platformMocks.disconnect.mockResolvedValue({
      revoked: false, manage_url: "https://myapps.microsoft.com", detail: null,
    });
    render(<IntegrationsPage />);
    const hold = await screen.findByRole("button", { name: /Hold to disconnect/ });
    fireEvent.pointerDown(hold);
    expect(
      await screen.findByText(/could not revoke DocMinds' access automatically/, {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(platformMocks.disconnect).toHaveBeenCalledWith("c1");
    expect(screen.getByRole("link", { name: "Open the account's app settings" })).toHaveAttribute(
      "href", "https://myapps.microsoft.com",
    );
  });

  it("lists imported documents and syncs one now", async () => {
    platformMocks.syncDocument.mockResolvedValue({ document_id: "d1", outcome: "unchanged", sync_status: "synced", last_error: null });
    render(<IntegrationsPage />);
    const table = await screen.findByRole("table", { name: "Imported documents" });
    expect(within(table).getByText("Up to date")).toBeInTheDocument();
    await userEvent.setup().click(within(table).getByRole("button", { name: "Sync now: Leave policy.docx" }));
    expect(await screen.findByText("No changes at the source.")).toBeInTheDocument();
  });

  it("says so when cloud imports are off", async () => {
    platformMocks.listProviders.mockRejectedValue(new ApiError("Not Found", 404));
    render(<IntegrationsPage />);
    expect(await screen.findByText("Cloud imports are not enabled on this server.")).toBeInTheDocument();
  });
});

describe("Admin dashboard", () => {
  it("shows totals, charts with data tables, and top questions", async () => {
    const { container } = render(<AdminDashboardPage />);
    expect(await screen.findByText("75%")).toBeInTheDocument();
    expect(screen.getAllByText("Questions answered per day").length).toBeGreaterThan(0);
    const docs = screen.getByRole("table", { name: "Documents processed and failed per day" });
    expect(within(docs).getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("What is the leave policy?")).toBeInTheDocument();
    const results = await axe(container);
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });

  it("reloads for another range", async () => {
    render(<AdminDashboardPage />);
    await screen.findByText("75%");
    await userEvent.setup().selectOptions(screen.getByLabelText("Time range"), "7");
    await waitFor(() => expect(platformMocks.getAnalytics).toHaveBeenLastCalledWith(7));
  });
});

describe("Settings links", () => {
  it("shows each feature only when it is on, plus the plan badge", async () => {
    render(<SettingsPage />);
    expect(await screen.findByRole("link", { name: "Free plan" }, { timeout: 5000 })).toHaveAttribute("href", "/settings/billing");
    const nav = await screen.findByRole("navigation", { name: "More settings" }, { timeout: 5000 });
    expect(within(nav).getByRole("link", { name: "Cloud imports" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Admin dashboard" })).toBeInTheDocument();
  });

  it("shows nothing when every feature is off", async () => {
    platformMocks.getBilling.mockRejectedValue(new ApiError("Not Found", 404));
    platformMocks.listProviders.mockRejectedValue(new ApiError("Not Found", 404));
    platformMocks.getAnalytics.mockRejectedValue(new ApiError("Not Found", 404));
    render(<SettingsPage />);
    await screen.findByRole("tab", { name: "Members" }, { timeout: 5000 });
    await waitFor(() => expect(platformMocks.getAnalytics).toHaveBeenCalled());
    expect(screen.queryByRole("navigation", { name: "More settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /plan$/ })).not.toBeInTheDocument();
  });
});
