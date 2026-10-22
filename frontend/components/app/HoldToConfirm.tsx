/**
 * HoldToConfirm.tsx  (F9)
 * =======================
 * A button that fires only after being HELD for `holdMs` -- a calmer
 * replacement for `window.confirm()` on destructive actions.
 *
 * WHY HOLD RATHER THAN A DIALOG
 * -----------------------------
 * A confirm dialog is clicked through on reflex; holding takes a deliberate
 * second and the filling bar shows exactly how close you are. Letting go
 * early cancels, so there is no "undo" to build.
 *
 * ACCESSIBILITY
 * -------------
 * - It is a real <button>, so it is focusable and announced as one.
 * - Keyboard: hold Enter or Space. Releasing early (or Escape) cancels.
 *   Auto-repeated keydowns are ignored so the timer does not restart.
 * - `aria-describedby` points at a hidden hint ("Press and hold for 1
 *   second to confirm"), so the interaction is explained, not discovered.
 * - A polite live region announces "Confirmed".
 * - A plain click does nothing on purpose; that is the safety property.
 * - Reduced motion: the fill bar does not animate (globals.css collapses
 *   transitions), but the hold time still has to elapse.
 */

"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

export interface HoldToConfirmProps {
  /** Runs once the hold completes. */
  onConfirm: () => void;
  children: ReactNode;
  /** How long to hold, in milliseconds. */
  holdMs?: number;
  /** Accessible name, when the visible content is only an icon. */
  "aria-label"?: string;
  className?: string;
  disabled?: boolean;
}

export default function HoldToConfirm({
  onConfirm,
  children,
  holdMs = 1000,
  className = "btn-ghost",
  disabled,
  ...rest
}: HoldToConfirmProps) {
  const [holding, setHolding] = useState(false);
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hintId = useId();

  function start() {
    if (disabled || timer.current) return;
    setHolding(true);
    setDone(false);
    timer.current = setTimeout(() => {
      timer.current = null;
      setHolding(false);
      setDone(true);
      onConfirm();
    }, holdMs);
  }

  function cancel() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setHolding(false);
  }

  // Never leave a timer running after unmount.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "Escape") return cancel();
    if (e.key !== "Enter" && e.key !== " ") return;
    // Stop the browser turning the key into a click.
    e.preventDefault();
    if (!e.repeat) start();
  }

  function onKeyUp(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "Enter" || e.key === " ") cancel();
  }

  const seconds = holdMs / 1000;
  return (
    <>
      <button
        type="button"
        {...rest}
        disabled={disabled}
        aria-describedby={hintId}
        data-holding={holding || undefined}
        className={`hold-confirm ${className}`}
        style={{ ["--hold-ms" as string]: `${holdMs}ms` }}
        onPointerDown={start}
        onPointerUp={cancel}
        onPointerLeave={cancel}
        onPointerCancel={cancel}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        onBlur={cancel}
        // Swallow the click a pointer release produces: only the hold counts.
        onClick={(e) => e.preventDefault()}
        onContextMenu={(e) => holding && e.preventDefault()}
      >
        <span className="hold-confirm-fill" aria-hidden="true" />
        <span className="relative inline-flex items-center gap-2">{children}</span>
      </button>
      <span id={hintId} className="sr-only">
        Press and hold for {seconds} second{seconds === 1 ? "" : "s"} to confirm
      </span>
      <span className="sr-only" aria-live="polite">
        {done ? "Confirmed" : ""}
      </span>
    </>
  );
}
