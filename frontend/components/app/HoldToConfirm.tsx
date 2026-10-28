// A button that fires only after being HELD for `holdMs` -- a calmer replacement for...

"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

export interface HoldToConfirmProps {
  onConfirm: () => void;
  children: ReactNode;
  holdMs?: number;
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

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "Escape") return cancel();
    if (e.key !== "Enter" && e.key !== " ") return;
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
