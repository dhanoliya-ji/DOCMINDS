/**
 * setup.ts
 * ========
 * Runs before every test file. Its job is to make each test start from a
 * clean, identical world.
 *
 * WHY THAT MATTERS MORE THAN IT SOUNDS
 * ------------------------------------
 * The three things reset below are all *module-level* or *global* state:
 * localStorage, the DOM, and the mocked router. Anything left behind in them
 * leaks into the next test, and the failure that results is the worst kind --
 * it depends on the order the tests happened to run in, so it passes alone and
 * fails in the suite, or passes locally and fails in CI.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

// ----------------------------------------------------------------------
// Next.js router
// ----------------------------------------------------------------------
// Every page here calls `useRouter()` and redirects with `router.push`.
// Outside a Next.js server there is no router, so the real hook throws.
//
// The mock is hoisted to module scope so a test can import `pushMock` and
// assert on it -- "did this page send an unauthenticated visitor to /login?"
// is a real behaviour worth checking, and this is how it becomes observable.
export const pushMock = vi.fn();
export const replaceMock = vi.fn();

// The query string `useSearchParams()` returns. A test that renders a page
// reading `?token=` or `?invite=` sets `searchParamsMock.value` first; it is
// reset to empty before every test below.
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

// ----------------------------------------------------------------------
// framer-motion
// ----------------------------------------------------------------------
// Animation adds nothing to a test and a great deal of noise: elements arrive
// mid-transition, so a query can find an item that is technically present but
// still at opacity 0. Replacing `motion.x` with a plain `<x>` renders the same
// content instantly and deterministically.
vi.mock("framer-motion", async () => {
  const React = await import("react");

  const passthrough = (tag: string) => {
    const Passthrough = ({ children, ...props }: any) => {
      // Strip the animation-only props so React does not warn about unknown
      // attributes on a real DOM element.
      const {
        initial, animate, exit, transition, variants, whileHover, whileTap,
        whileInView, viewport, layout, layoutId, ...rest
      } = props;
      return React.createElement(tag, rest, children);
    };
    // A displayName is what makes a failure read "MotionDiv" rather than
    // "Anonymous" in a rendered-DOM dump -- which is most of what you have to
    // go on when a component test fails.
    Passthrough.displayName = `Motion${tag[0].toUpperCase()}${tag.slice(1)}`;
    return Passthrough;
  };

  const AnimatePresenceStub = ({ children }: { children: React.ReactNode }) =>
    children;
  AnimatePresenceStub.displayName = "AnimatePresence";

  return {
    // CACHED per tag: `motion.div` must return the SAME component every time.
    // A fresh component type on each render makes React unmount and remount
    // everything inside it -- so a controlled <input> wrapped in motion.div
    // lost its focus after every keystroke and a test typing into it only
    // ever got the first character in.
    motion: new Proxy({} as Record<string, unknown>, {
      get: (cache, tag: string) => (cache[tag] ??= passthrough(tag)),
    }),
    AnimatePresence: AnimatePresenceStub,
    useAnimation: () => ({ start: vi.fn(), stop: vi.fn() }),
  };
});

// ----------------------------------------------------------------------
// window.location
// ----------------------------------------------------------------------
// api.ts redirects with `window.location.href = "/login"` when a request comes
// back 401. jsdom cannot navigate, so it logs "Not implemented: navigation to
// another Document" and carries on -- noise that buries real output.
//
// Replacing `href` with a plain writable property does two things: it silences
// that, and it turns the redirect into something a test can ASSERT on. Where
// an unauthenticated user is sent is behaviour, not an implementation detail.
const originalLocation = window.location;

// ----------------------------------------------------------------------
// Element.scrollIntoView
// ----------------------------------------------------------------------
// jsdom implements no layout at all, so this method simply does not exist.
// ChatPanel calls it after every message to keep the newest answer in view,
// which means every one of its tests would fail with "not a function" -- an
// error that points at the component rather than at the missing browser API.
//
// A no-op is the right stub: there is nothing to scroll in a headless DOM, and
// whether the view scrolled is not something these tests can meaningfully
// assert on anyway.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

beforeEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { ...originalLocation, href: "http://localhost:3000/", assign: vi.fn() },
  });

  // A token left behind by one test would silently authenticate the next one.
  localStorage.clear();
  vi.clearAllMocks();
  searchParamsMock.value = new URLSearchParams();
});

afterEach(() => {
  // Unmounts every rendered component. Without it the DOM accumulates, and a
  // `getByText` that should match once starts finding several.
  cleanup();
});
