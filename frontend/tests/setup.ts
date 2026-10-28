// Runs before every test file.

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

export const pushMock = vi.fn();
export const replaceMock = vi.fn();

export const searchParamsMock = { value: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
    replace: replaceMock,
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => searchParamsMock.value,
  useParams: () => ({}),
}));

vi.mock("framer-motion", async () => {
  const React = await import("react");

  const passthrough = (tag: string) => {
    const Passthrough = ({ children, ...props }: any) => {
      const {
        initial, animate, exit, transition, variants, whileHover, whileTap,
        whileInView, viewport, layout, layoutId, ...rest
      } = props;
      return React.createElement(tag, rest, children);
    };
    Passthrough.displayName = `Motion${tag[0].toUpperCase()}${tag.slice(1)}`;
    return Passthrough;
  };

  const AnimatePresenceStub = ({ children }: { children: React.ReactNode }) =>
    children;
  AnimatePresenceStub.displayName = "AnimatePresence";

  return {
    motion: new Proxy({} as Record<string, unknown>, {
      get: (cache, tag: string) => (cache[tag] ??= passthrough(tag)),
    }),
    AnimatePresence: AnimatePresenceStub,
    useAnimation: () => ({ start: vi.fn(), stop: vi.fn() }),
  };
});

const originalLocation = window.location;

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

beforeEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { ...originalLocation, href: "http://localhost:3000/", assign: vi.fn() },
  });

  localStorage.clear();
  vi.clearAllMocks();
  searchParamsMock.value = new URLSearchParams();
});

afterEach(() => {
  cleanup();
});
