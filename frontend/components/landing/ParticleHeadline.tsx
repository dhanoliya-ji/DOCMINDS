/**
 * ParticleHeadline.tsx  (F5: headline that scatters and reforms)
 * ==============================================================
 * The hero <h1> is sampled into a few thousand dots on a canvas. Moving the
 * cursor near the text pushes dots away; each dot springs back to its home
 * position, so the headline "reforms" behind the pointer. On load the dots
 * fly in from random positions.
 *
 * ACCESSIBILITY
 * -------------
 * The real <h1> text stays in the DOM, always. The canvas is aria-hidden and
 * the text is merely made transparent while the canvas draws it, so screen
 * readers, search engines, find-in-page and text selection all still work.
 * Under reduced motion (or with no canvas support) the plain gradient
 * heading is shown and no canvas is created.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { cappedDpr, useIsActive, usePrefersReducedMotion } from "./hooks";

interface ParticleHeadlineProps {
  lines: string[];
  className?: string;
}

interface Dot {
  x: number;
  y: number;
  hx: number;
  hy: number;
  vx: number;
  vy: number;
}

export default function ParticleHeadline({ lines, className = "" }: ParticleHeadlineProps) {
  const reduced = usePrefersReducedMotion();
  const wrapRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const active = useIsActive(wrapRef);
  // Becomes true once the canvas has successfully drawn, so the DOM text is
  // only hidden when there is something to replace it visually.
  const [drawing, setDrawing] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    const heading = headingRef.current;
    if (reduced || !active || !canvas || !heading) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = cappedDpr(2);
    const rect = heading.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    if (w === 0 || h === 0) return;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    // Sit exactly over the heading, which may be centred inside the wrapper.
    canvas.style.left = `${heading.offsetLeft}px`;
    canvas.style.top = `${heading.offsetTop}px`;

    // 1. Rasterise the heading off screen using its computed font, then
    //    sample opaque pixels on a grid to get each dot's home position.
    const style = getComputedStyle(heading);
    const off = document.createElement("canvas");
    off.width = w;
    off.height = h;
    const octx = off.getContext("2d")!;
    octx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    octx.textAlign = "center";
    octx.textBaseline = "middle";
    octx.fillStyle = "#fff";
    const lineH = h / lines.length;
    lines.forEach((line, i) => octx.fillText(line, w / 2, lineH * (i + 0.5)));
    const data = octx.getImageData(0, 0, w, h).data;
    const step = w < 500 ? 3 : 4;
    const dots: Dot[] = [];
    for (let y = 0; y < h; y += step) {
      for (let x = 0; x < w; x += step) {
        if (data[(y * w + x) * 4 + 3] > 128) {
          dots.push({
            hx: x,
            hy: y,
            x: Math.random() * w,
            y: Math.random() * h,
            vx: 0,
            vy: 0,
          });
        }
      }
    }

    const grad = ctx.createLinearGradient(0, 0, w * dpr, 0);
    grad.addColorStop(0, "#a5b4fc");
    grad.addColorStop(0.5, "#c084fc");
    grad.addColorStop(1, "#67e8f9");

    // 2. Pointer position in canvas coordinates; far away when outside.
    const mouse = { x: -9999, y: -9999 };
    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      mouse.x = e.clientX - r.left;
      mouse.y = e.clientY - r.top;
    };
    const onLeave = () => {
      mouse.x = mouse.y = -9999;
    };
    window.addEventListener("pointermove", onMove);
    document.addEventListener("pointerleave", onLeave);

    // 3. Spring physics: each dot is pulled home and pushed from the cursor.
    let frame = 0;
    let first = true;
    const R = 70;
    const tick = () => {
      if (first) {
        // Hide the DOM text only once the canvas is really drawing.
        first = false;
        setDrawing(true);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = grad;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      for (const d of dots) {
        const dx = d.x - mouse.x;
        const dy = d.y - mouse.y;
        const dist2 = dx * dx + dy * dy;
        if (dist2 < R * R) {
          const f = (1 - Math.sqrt(dist2) / R) * 3;
          d.vx += (dx / (Math.sqrt(dist2) + 0.01)) * f;
          d.vy += (dy / (Math.sqrt(dist2) + 0.01)) * f;
        }
        d.vx = (d.vx + (d.hx - d.x) * 0.06) * 0.82;
        d.vy = (d.vy + (d.hy - d.y) * 0.06) * 0.82;
        d.x += d.vx;
        d.y += d.vy;
        ctx.fillRect(d.x * dpr, d.y * dpr, 1.6 * dpr, 1.6 * dpr);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
    };
  }, [reduced, active, lines]);

  const showCanvas = !reduced;

  return (
    <div ref={wrapRef} className="relative">
      <h1
        ref={headingRef}
        className={`${className} ${showCanvas && drawing ? "text-transparent" : "text-gradient"}`}
        // Transparent text stays selectable and readable by assistive tech.
        style={showCanvas && drawing ? { WebkitTextFillColor: "transparent" } : undefined}
      >
        {lines.map((line, i) => (
          <span key={line} className="block">
            {line}
            {i < lines.length - 1 ? " " : ""}
          </span>
        ))}
      </h1>
      {showCanvas && (
        <canvas
          ref={canvasRef}
          aria-hidden="true"
          data-testid="particle-canvas"
          className="pointer-events-none absolute left-0 top-0"
        />
      )}
    </div>
  );
}
