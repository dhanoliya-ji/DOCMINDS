// * /developers: the API docs page -- every section, the language tabs on the examples...

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import DevelopersPage from "@/app/developers/page";
import PwaSupport from "@/components/PwaSupport";
import { setLocale } from "@/lib/i18n";

describe("/developers", () => {
  it("documents keys, auth, limits, endpoints, examples and errors", () => {
    render(<DevelopersPage />);
    expect(screen.getByRole("heading", { level: 1, name: "DocMinds API" })).toBeInTheDocument();
    for (const name of [/Create an API key/, /Authenticate/, /Rate limits/, /Endpoints/, /Examples/, /Errors/]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.getByText("POST /api/v1/search/")).toBeInTheDocument();
    expect(screen.getByText("60 / minute")).toBeInTheDocument();
    expect(screen.getAllByText("X-API-Key").length).toBeGreaterThan(0);
  });

  it("switches the examples between curl, Python and TypeScript", async () => {
    const user = userEvent.setup();
    render(<DevelopersPage />);
    expect(screen.getAllByText(/curl http/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Python" }));
    expect(screen.getByText(/import os, requests/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "TypeScript" }));
    expect(screen.getByRole("button", { name: "TypeScript" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/const api = /)).toBeInTheDocument();
  });

  it("copies an example", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<DevelopersPage />);
    await user.click(screen.getByRole("button", { name: "Copy the List your projects example" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("/api/v1/projects/"));
  });

  it("follows the language switcher", () => {
    render(<DevelopersPage />);
    act(() => setLocale("hi"));
    expect(screen.getByRole("heading", { level: 2, name: /API कुंजी बनाएँ/ })).toBeInTheDocument();
    act(() => setLocale("en"));
  });
});

describe("PwaSupport", () => {
  function offerInstall() {
    const event = new Event("beforeinstallprompt") as Event & {
      prompt: () => Promise<void>;
      userChoice: Promise<{ outcome: string }>;
    };
    event.prompt = vi.fn().mockResolvedValue(undefined);
    event.userChoice = Promise.resolve({ outcome: "accepted" });
    act(() => {
      window.dispatchEvent(event);
    });
    return event;
  }

  it("shows nothing until the browser offers installation", () => {
    const { container } = render(<PwaSupport />);
    expect(container).toBeEmptyDOMElement();
  });

  it("installs through the browser's own prompt", async () => {
    const user = userEvent.setup();
    render(<PwaSupport />);
    const event = offerInstall();
    await user.click(screen.getByRole("button", { name: "Install app" }));
    expect(event.prompt).toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Install DocMinds" })).not.toBeInTheDocument();
  });

  it("remembers a dismissal", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<PwaSupport />);
    offerInstall();
    await user.click(screen.getAllByRole("button", { name: "Not now" })[0]);
    unmount();
    render(<PwaSupport />);
    offerInstall();
    expect(screen.queryByRole("region", { name: "Install DocMinds" })).not.toBeInTheDocument();
  });
});

