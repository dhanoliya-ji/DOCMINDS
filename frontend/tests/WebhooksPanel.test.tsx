// Settings -> Webhooks.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "@/app/settings/page";
import WebhooksPanel from "@/components/settings/WebhooksPanel";
import { ApiError } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  getMe: vi.fn(),
  listMembers: vi.fn(),
  listInvites: vi.fn(),
  listWebhooks: vi.fn(),
  listWebhookEvents: vi.fn(),
  createWebhook: vi.fn(),
  deleteWebhook: vi.fn(),
  testWebhook: vi.fn(),
  listWebhookDeliveries: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...mocks } };
});

const ADMIN = {
  id: "admin-1", email: "ada@example.com", role: "Admin", org_id: "o", is_active: true,
  is_verified: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};

const HOOK = {
  id: "w1", url: "https://example.com/hook", description: "CRM sync",
  event_types: ["document.processed"], is_active: true, created_at: "2026-01-01T00:00:00Z",
};

async function hold(button: HTMLElement) {
  fireEvent.pointerDown(button);
  await act(() => new Promise((r) => setTimeout(r, 1100)));
  fireEvent.pointerUp(button);
}

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
  localStorage.setItem("edia_token", "a-token");
  mocks.getMe.mockResolvedValue(ADMIN);
  mocks.listMembers.mockResolvedValue([ADMIN]);
  mocks.listInvites.mockResolvedValue([]);
  mocks.listWebhooks.mockResolvedValue([HOOK]);
  mocks.listWebhookEvents.mockResolvedValue([
    { name: "document.processed", description: "A document finished processing" },
    { name: "member.joined", description: "Someone joined" },
  ]);
  mocks.listWebhookDeliveries.mockResolvedValue([]);
});

describe("the settings tab", () => {
  it("appears for an admin when webhooks are on", async () => {
    render(<SettingsPage />);
    const tab = await screen.findByRole("tab", { name: "Webhooks" }, { timeout: 5000 });
    await userEvent.setup().click(tab);
    expect(await screen.findByText("https://example.com/hook", {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it("is hidden when the API answers 404 (webhooks off)", async () => {
    mocks.listWebhooks.mockRejectedValue(new ApiError("Not Found", 404));
    render(<SettingsPage />);
    await screen.findByRole("tab", { name: "Members" });
    await waitFor(() => expect(mocks.listWebhooks).toHaveBeenCalled());
    expect(screen.queryByRole("tab", { name: "Webhooks" })).not.toBeInTheDocument();
  });

  it("is never offered to a non-admin", async () => {
    mocks.getMe.mockResolvedValue({ ...ADMIN, role: "Manager" });
    render(<SettingsPage />);
    await screen.findByText(/managed by your/);
    expect(screen.queryByRole("tab", { name: "Webhooks" })).not.toBeInTheDocument();
    expect(mocks.listWebhooks).not.toHaveBeenCalled();
  });
});

describe("WebhooksPanel", () => {
  it("renders nothing on a 404", async () => {
    mocks.listWebhooks.mockRejectedValue(new ApiError("Not Found", 404));
    const { container } = render(<WebhooksPanel />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("shows other failures as an alert", async () => {
    mocks.listWebhooks.mockRejectedValue(new ApiError("Boom", 500));
    render(<WebhooksPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Boom");
  });

  it("lists endpoints with their events and state", async () => {
    render(<WebhooksPanel />);
    const list = await screen.findByRole("list", { name: "Webhooks" });
    expect(within(list).getByText("CRM sync")).toBeInTheDocument();
    expect(within(list).getByText(/document\.processed/)).toBeInTheDocument();
    expect(within(list).getByText("Active")).toBeInTheDocument();
  });

  it("creates one for chosen events and shows the secret once", async () => {
    const user = userEvent.setup();
    mocks.createWebhook.mockResolvedValue({ ...HOOK, id: "w2", secret: "whsec_123" });
    render(<WebhooksPanel />);
    await screen.findByText("https://example.com/hook");

    await user.type(screen.getByLabelText("Endpoint URL"), "https://other.example/hook");
    await user.click(screen.getByLabelText("All events"));
    await user.click(await screen.findByLabelText("member.joined"));
    await user.click(screen.getByRole("button", { name: /add webhook/i }));

    await waitFor(() =>
      expect(mocks.createWebhook).toHaveBeenCalledWith("https://other.example/hook", ["member.joined"], undefined)
    );
    expect(await screen.findByTestId("new-webhook-secret")).toHaveTextContent("whsec_123");
    expect(screen.getByText(/will not be shown again/)).toBeInTheDocument();
  });

  it("sends a test event and shows the deliveries", async () => {
    const user = userEvent.setup();
    mocks.testWebhook.mockResolvedValue({});
    mocks.listWebhookDeliveries.mockResolvedValue([
      {
        id: "d1", event_type: "ping", status: "failed", attempts: 3, last_status_code: 500,
        last_error: "Server error", created_at: "2026-01-02T00:00:00Z", payload: {},
      },
    ]);
    render(<WebhooksPanel />);
    await user.click(await screen.findByRole("button", { name: /send a test event/i }));
    await waitFor(() => expect(mocks.testWebhook).toHaveBeenCalledWith("w1"));
    expect((await screen.findAllByText(/test event queued/i)).length).toBeGreaterThan(0);

    const toggle = screen.getByRole("button", { name: /deliveries/i });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("Server error", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("deletes only after a completed hold", async () => {
    mocks.deleteWebhook.mockResolvedValue({});
    render(<WebhooksPanel />);
    const remove = await screen.findByRole("button", { name: /delete the webhook/i });
    fireEvent.click(remove);
    expect(mocks.deleteWebhook).not.toHaveBeenCalled();
    await hold(remove);
    await waitFor(() => expect(mocks.deleteWebhook).toHaveBeenCalledWith("w1"));
  });
});
