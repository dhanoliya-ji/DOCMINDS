/**
 * LandingPage.test.tsx
 * ====================
 * The landing page and its reduced-motion fallbacks.
 *
 * WHAT IS MOCKED, AND WHY
 * -----------------------
 * - The R3F orb scene and Lenis: jsdom has no WebGL, and these chunks are
 *   lazy/optional anyway. Mocking them also keeps the suite runnable before
 *   three.js is installed.
 * - next/dynamic: renders the `loading` poster, which is exactly what a real
 *   visitor sees before the 3D chunk arrives.
 * - framer-motion is UN-mocked: setup.ts stubs it globally with plain
 *   elements, but these components need its real hooks (useScroll,
 *   useTransform), and the reduced-motion branches are what is under test.
 * - window.matchMedia: jsdom does not implement it, and it is how every
 *   component learns about prefers-reduced-motion and hover support.
 */

import { render, screen, within } from "@testing-library/react";
import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("framer-motion", async (importOriginal) => await importOriginal());
vi.mock("@/components/landing/OrbScene", () => ({
  default: () => <div data-testid="orb-scene" />,
}));
vi.mock("@/components/landing/SmoothScroll", () => ({
  default: () => null,
}));
vi.mock("next/dynamic", () => ({
  default: (_load: unknown, opts: { loading: ComponentType }) => opts.loading,
}));

import LandingPage from "@/app/page";
import Flashlight from "@/components/landing/Flashlight";
import PipelineScroll from "@/components/landing/PipelineScroll";
import { setToken } from "@/lib/api";

/** Stub matchMedia so the given queries match and every other one does not. */
function mockMedia({ reduced = false, hover = false } = {}) {
  window.matchMedia = vi.fn((query: string) => ({
    matches:
      (query.includes("prefers-reduced-motion") && reduced) ||
      (query.includes("hover") && hover),
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

describe("LandingPage", () => {
  it("renders the real h1 text and section headings", () => {
    mockMedia();
    render(<LandingPage />);
    const h1 = screen.getByRole("heading", { level: 1 });
    // Read by screen readers even while the particle canvas draws over it.
    expect(h1).toHaveTextContent("Ask your documents anything.");
    for (const name of [
      "Built for real document piles",
      "How a question gets answered",
      "See exactly where the answer came from",
      "Answers you can check",
    ]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
  });

  it("sends a signed-out visitor to /login", () => {
    mockMedia();
    render(<LandingPage />);
    const ctas = screen.getAllByRole("link", { name: /get started free|start asking/i });
    expect(ctas).toHaveLength(2);
    ctas.forEach((a) => expect(a).toHaveAttribute("href", "/login"));
    expect(screen.getByRole("link", { name: /view source/i })).toHaveAttribute(
      "rel",
      "noopener noreferrer"
    );
  });

  it("sends a signed-in visitor to the dashboard", () => {
    mockMedia();
    setToken("t");
    render(<LandingPage />);
    const links = screen.getAllByRole("link", { name: /open dashboard/i });
    expect(links.length).toBeGreaterThan(0);
    links.forEach((a) => expect(a).toHaveAttribute("href", "/dashboard"));
  });

  it("shows only static fallbacks under prefers-reduced-motion", () => {
    mockMedia({ reduced: true });
    render(<LandingPage />);
    expect(screen.getByTestId("orb-poster")).toBeInTheDocument();
    expect(screen.queryByTestId("particle-canvas")).not.toBeInTheDocument();
    expect(screen.getByTestId("mesh-gradient").querySelector("canvas")).toBeNull();
    expect(screen.getByTestId("pipeline-static")).toBeInTheDocument();
    expect(screen.queryByTestId("pipeline-animated")).not.toBeInTheDocument();
    expect(screen.getByTestId("parallax-cards")).toHaveAttribute("data-mode", "static");
    expect(screen.getByTestId("stacking-cards")).toHaveAttribute("data-mode", "static");
    expect(screen.getByTestId("flashlight")).toHaveAttribute("data-mode", "static");
  });

  it("enables the animated versions when motion is allowed", () => {
    mockMedia({ reduced: false, hover: true });
    render(<LandingPage />);
    expect(screen.getByTestId("particle-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-animated")).toBeInTheDocument();
    expect(screen.getByTestId("parallax-cards")).toHaveAttribute("data-mode", "parallax");
    expect(screen.getByTestId("flashlight")).toHaveAttribute("data-mode", "interactive");
  });
});

describe("Flashlight", () => {
  it("is static on touch devices, with the citation visible", () => {
    mockMedia({ hover: false });
    render(<Flashlight />);
    const fig = screen.getByTestId("flashlight");
    expect(fig).toHaveAttribute("data-mode", "static");
    expect(within(fig).getByText(/25 days of paid annual leave/)).toBeInTheDocument();
  });
});

describe("PipelineScroll", () => {
  it("lists all four stages in order when reduced motion is on", () => {
    mockMedia({ reduced: true });
    render(<PipelineScroll />);
    const items = within(screen.getByRole("list")).getAllByRole("heading", { level: 3 });
    expect(items.map((h) => h.textContent?.replace(/^\d+\s*/, ""))).toEqual([
      "Upload",
      "Chunk",
      "Embed",
      "Answer",
    ]);
  });
});
