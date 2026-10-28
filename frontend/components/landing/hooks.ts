// Small hooks every animated landing component leans on.

"use client";

import { type RefObject, useEffect, useState, useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";
const HOVER = "(hover: hover) and (pointer: fine)";

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

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReduced, () => matches(REDUCED), () => true);
}

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

export function cappedDpr(max = 1.5): number {
  if (typeof window === "undefined") return 1;
  return Math.min(window.devicePixelRatio || 1, max);
}
