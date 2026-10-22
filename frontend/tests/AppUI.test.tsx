/**
 * AppUI.test.tsx
 * ==============
 * The signed-in app's shared UI pieces (components/app/* and lib/theme.ts):
 *
 *   * theme       stored choice, system fallback, storage that throws, and
 *                 the no-flash inline script agreeing with the runtime code
 *   * HoldToConfirm  fires only after a full hold, by pointer or keyboard;
 *                 a click, an early release or Escape cancels
 *   * AppShell    nav marks the current page, the mobile drawer opens and
 *                 closes accessibly, sign-out signs out
 *   * Onboarding  the tour steps through, and dismissal is remembered
 *   * Cards / loader  render the F12/F14/F18 hooks the CSS keys off
 *
 * Fake timers for the hold tests, so "one second" costs nothing.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppShell from "@/components/app/AppShell";
import HoldToConfirm from "@/components/app/HoldToConfirm";
import LogoLoader from "@/components/app/LogoLoader";
import { FirstRunEmpty, GuidedTour, isTourDone } from "@/components/app/Onboarding";
import ProjectCard from "@/components/app/ProjectCard";
import ThemeToggle from "@/components/app/ThemeToggle";
import {
  THEME_INIT_SCRIPT,
  THEME_STORAGE_KEY,
  applyTheme,
  readThemeChoice,
  resolveTheme,
  writeThemeChoice,
} from "@/lib/theme";
import { pushMock } from "./setup";

const logout = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, logout: () => logout() } };
});

/** Make `prefers-color-scheme: light` (or nothing) match. */
function stubColorScheme(light: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: light && query.includes("light"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  // @ts-expect-error -- jsdom has no matchMedia; put it back to absent.
  delete window.matchMedia;
  delete document.documentElement.dataset.theme;
});

// ======================================================================
// Theme
// ======================================================================
describe("theme", () => {
  it("defaults to system, and system follows the OS", () => {
    expect(readThemeChoice()).toBe("system");
    stubColorScheme(true);
    expect(resolveTheme("system")).toBe("light");
    stubColorScheme(false);
    expect(resolveTheme("system")).toBe("dark");
  });

  it("persists and applies an explicit choice", () => {
    writeThemeChoice("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(readThemeChoice()).toBe("light");
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("ignores garbage in storage", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "purple");
    expect(readThemeChoice()).toBe("system");
  });

  it("survives storage that throws (Safari private mode)", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(readThemeChoice()).toBe("system");
    expect(() => writeThemeChoice("dark")).not.toThrow();
    get.mockRestore();
    set.mockRestore();
  });

  it("the inline no-flash script resolves the same way as the runtime", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    new Function(THEME_INIT_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe("light");

    localStorage.setItem(THEME_STORAGE_KEY, "system");
    stubColorScheme(false);
    new Function(THEME_INIT_SCRIPT)();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("the toggle cycles system -> light -> dark and remembers it", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /Theme: System/ });

    await user.click(button);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(button).toHaveAccessibleName(/Theme: Light\. Switch to Dark/);

    await user.click(button);
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});

// ======================================================================
// HoldToConfirm (F9)
// ======================================================================
describe("HoldToConfirm", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function setup() {
    const onConfirm = vi.fn();
    render(
      <HoldToConfirm onConfirm={onConfirm} aria-label="Delete thing">
        Delete
      </HoldToConfirm>
    );
    return { onConfirm, button: screen.getByRole("button", { name: "Delete thing" }) };
  }

  it("explains itself to assistive tech", () => {
    const { button } = setup();
    expect(button).toHaveAccessibleDescription("Press and hold for 1 second to confirm");
  });

  it("fires after a full pointer hold, and announces it", () => {
    const { onConfirm, button } = setup();
    fireEvent.pointerDown(button);
    expect(button).toHaveAttribute("data-holding");
    act(() => vi.advanceTimersByTime(1000));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
  });

  it("does nothing on a plain click or an early release", () => {
    const { onConfirm, button } = setup();
    fireEvent.click(button);
    fireEvent.pointerDown(button);
    act(() => vi.advanceTimersByTime(600));
    fireEvent.pointerUp(button);
    act(() => vi.advanceTimersByTime(1000));
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("works from the keyboard: hold Space or Enter; repeats do not restart", () => {
    const { onConfirm, button } = setup();
    fireEvent.keyDown(button, { key: " " });
    act(() => vi.advanceTimersByTime(500));
    fireEvent.keyDown(button, { key: " ", repeat: true });
    act(() => vi.advanceTimersByTime(500));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(button, { key: "Enter" });
    act(() => vi.advanceTimersByTime(300));
    fireEvent.keyUp(button, { key: "Enter" });
    act(() => vi.advanceTimersByTime(1000));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("Escape cancels a hold in progress", () => {
    const { onConfirm, button } = setup();
    fireEvent.keyDown(button, { key: "Enter" });
    fireEvent.keyDown(button, { key: "Escape" });
    act(() => vi.advanceTimersByTime(2000));
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

// ======================================================================
// AppShell
// ======================================================================
describe("AppShell", () => {
  it("renders the page inside a main landmark with a skip link", () => {
    render(<AppShell>hello</AppShell>);
    expect(screen.getByRole("main")).toHaveTextContent("hello");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("opens and closes the mobile drawer accessibly", async () => {
    const user = userEvent.setup();
    render(<AppShell>x</AppShell>);
    const open = screen.getByRole("button", { name: "Open menu" });
    expect(open).toHaveAttribute("aria-expanded", "false");

    await user.click(open);
    const drawer = screen.getByRole("dialog", { name: "Menu" });
    expect(drawer).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument();
  });

  it("signs out", async () => {
    const user = userEvent.setup();
    render(<AppShell>x</AppShell>);
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(logout).toHaveBeenCalled();
    expect(pushMock).toHaveBeenCalledWith("/login");
  });
});

// ======================================================================
// Onboarding
// ======================================================================
describe("onboarding", () => {
  const steps = [
    { title: "One", body: "first" },
    { title: "Two", body: "second" },
  ];

  it("steps through the tour and remembers it is done", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<GuidedTour id="t" steps={steps} />);
    expect(await screen.findByRole("region", { name: "One" })).toHaveTextContent("step 1 of 2");

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("region", { name: "Two" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Got it" }));

    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(isTourDone("t")).toBe(true);

    unmount();
    render(<GuidedTour id="t" steps={steps} />);
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });

  it("can be dismissed outright", async () => {
    const user = userEvent.setup();
    render(<GuidedTour id="d" steps={steps} />);
    await user.click(await screen.findByRole("button", { name: "Dismiss tour" }));
    expect(isTourDone("d")).toBe(true);
  });

  it("first-run suggestions are clickable", async () => {
    const pick = vi.fn();
    const user = userEvent.setup();
    render(
      <FirstRunEmpty icon={null} title="Empty" body="b" suggestions={["HR Policies"]} onSuggestion={pick} />
    );
    await user.click(screen.getByRole("button", { name: "HR Policies" }));
    expect(pick).toHaveBeenCalledWith("HR Policies");
  });
});

// ======================================================================
// Cards and loader
// ======================================================================
describe("cards and loader", () => {
  it("project card links to the workspace with hover preview and blur reveal", () => {
    render(<ProjectCard project={{ id: "p1", name: "HR", created_at: "2026-01-01T00:00:00Z" } as never} />);
    const card = screen.getByRole("link", { name: /HR/ });
    expect(card).toHaveAttribute("href", "/project/p1");
    expect(card).toHaveClass("lift-card");
    expect(card.querySelector(".card-preview")).not.toBeNull();
    expect(card.querySelector(".blur-reveal")).not.toBeNull();
  });

  it("logo loader is a labelled status", () => {
    render(<LogoLoader label="Loading projects" />);
    expect(screen.getByRole("status", { name: "Loading projects" })).toBeInTheDocument();
  });
});
