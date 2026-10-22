/**
 * hooks.ts  (shared landing-page hooks)
 * =====================================
 * Small hooks every animated landing component leans on.
 *
 * usePrefersReducedMotion
 * -----------------------
 * Mirrors the OS "reduce motion" setting. Every effect on the landing page
 * checks it and falls back to a static rendering when it is on -- large
 * parallax and particle motion can be genuinely nauseating for people with
 * vestibular disorders.
 *
 * useIsActive
 * -----------
 * True only while an element is on screen AND the tab is visible. Canvas and
 * WebGL loops use it to stop drawing when nobody can see them, which is the
 * single biggest battery and CPU saving on a page like this (F19).
 */

"use client";

import { type RefObject, useEffect, useState, useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";
const HOVER = "(hover: hover) and (pointer: fine)";

/** Build a useSyncExternalStore subscription for one media query. */
function mediaStore(query: string) {
  return (onChange: () => void): (() => void) => {
    if (typeof window === "undefined" || !window.matchMedia) return () => {};
    const mql = window.matchMedia(query);
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  };
}

const subscribeReduced = mediaStore(REDUCED);
const subscribeHover = mediaStore(HOVER);

function matches(query: string): boolean {
  return typeof window !== "undefined" && !!window.matchMedia
    ? window.matchMedia(query).matches
    : false;
}

/**
 * The server snapshot is `true`: the server cannot know the visitor's
 * preference, and rendering the static version first is the safe default --
 * motion is switched ON after hydration rather than switched off.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReduced, () => matches(REDUCED), () => true);
}

/** True when the primary pointer can hover (mouse/trackpad, not touch). */
export function useCanHover(): boolean {
  return useSyncExternalStore(subscribeHover, () => matches(HOVER), () => false);
}

export function useIsActive(ref: RefObject<Element | null>): boolean {
  const [onScreen, setOnScreen] = useState(false);
  const [tabVisible, setTabVisible] = useState(true);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      ([entry]) => setOnScreen(entry.isIntersecting),
      { rootMargin: "100px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);

  useEffect(() => {
    const onVis = () => setTabVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  return onScreen && tabVisible;
}

/** Device pixel ratio capped at `max`, so 3x phones do not render 9x pixels. */
export function cappedDpr(max = 1.5): number {
  if (typeof window === "undefined") return 1;
  return Math.min(window.devicePixelRatio || 1, max);
}
