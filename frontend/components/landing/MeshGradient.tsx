/**
 * MeshGradient.tsx  (F1: animated WebGL background)
 * =================================================
 * A full-bleed, slowly flowing colour field behind the hero.
 *
 * WHY A HAND-WRITTEN SHADER, NOT THREE.JS
 * ---------------------------------------
 * The effect is one full-screen triangle and ~20 lines of GLSL. Pulling in a
 * 3D engine for it would cost far more than it draws, and it should be visible
 * on first paint, before the lazy-loaded 3D chunk arrives.
 *
 * Colours come from the CSS variables in globals.css (--bg-base, --accent,
 * --violet, --cyan), so the background follows the theme. The loop stops
 * when the canvas is off screen or the tab is hidden, and never starts under
 * prefers-reduced-motion -- the static CSS gradient underneath shows instead.
 */

"use client";

import { useEffect, useRef } from "react";

import { cappedDpr, useIsActive, usePrefersReducedMotion } from "./hooks";

const VERT = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";

// Three colour blobs whose centres drift on slow sine paths, blended by
// inverse distance over the base colour. Cheap, smooth, never visibly loops.
const FRAG = `precision mediump float;
uniform vec2 r;uniform float t;uniform vec3 c0,c1,c2,c3;
void main(){
  vec2 uv=gl_FragCoord.xy/r;uv.x*=r.x/r.y;
  vec2 a=vec2(.3+.2*sin(t*.11),.7+.15*cos(t*.13));
  vec2 b=vec2(1.1+.25*cos(t*.09),.35+.2*sin(t*.12));
  vec2 c=vec2(.8+.3*sin(t*.07+1.),.9+.1*cos(t*.1));
  float wa=1./(.02+pow(distance(uv,a),2.));
  float wb=1./(.02+pow(distance(uv,b),2.));
  float wc=1./(.02+pow(distance(uv,c),2.));
  float wd=6.;
  gl_FragColor=vec4((c1*wa+c2*wb+c3*wc+c0*wd)/(wa+wb+wc+wd),1.);
}`;

type RGB = [number, number, number];

/** Read a `#rrggbb` CSS variable as normalised RGB, or use the fallback. */
function cssColor(name: string, fallback: RGB): RGB {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  const m = raw.match(/^#([0-9a-f]{6})$/i);
  if (!m) return fallback;
  const n = parseInt(m[1], 16);
  return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export default function MeshGradient() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduced = usePrefersReducedMotion();
  // Observed on the always-rendered wrapper: the canvas itself only mounts
  // after hydration, too late for an observer attached on first mount.
  const wrapRef = useRef<HTMLDivElement>(null);
  const active = useIsActive(wrapRef);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (reduced || !active || !canvas) return;
    const gl = canvas.getContext("webgl", { antialias: false });
    if (!gl) return; // No WebGL: the CSS gradient fallback stays visible.

    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      return s;
    };
    const prog = gl.createProgram()!;
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    gl.useProgram(prog);

    // One oversized triangle covers the screen with fewer vertices than a quad.
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW
    );
    const loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    const u = (n: string) => gl.getUniformLocation(prog, n);
    gl.uniform3fv(u("c0"), cssColor("--bg-base", [0.03, 0.04, 0.09]));
    gl.uniform3fv(u("c1"), cssColor("--accent", [0.39, 0.4, 0.95]));
    gl.uniform3fv(u("c2"), cssColor("--violet", [0.66, 0.33, 0.97]));
    gl.uniform3fv(u("c3"), cssColor("--cyan", [0.13, 0.83, 0.93]));

    const resize = () => {
      // Half resolution: the field is soft, nobody can tell, and it quarters
      // the fragment cost.
      const scale = cappedDpr(1.5) * 0.5;
      canvas.width = Math.max(1, Math.round(canvas.clientWidth * scale));
      canvas.height = Math.max(1, Math.round(canvas.clientHeight * scale));
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(u("r"), canvas.width, canvas.height);
    };
    resize();
    window.addEventListener("resize", resize);

    let frame = 0;
    const start = performance.now();
    const draw = (now: number) => {
      gl.uniform1f(u("t"), (now - start) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
    };
  }, [reduced, active]);

  return (
    <div
      ref={wrapRef}
      aria-hidden="true"
      data-testid="mesh-gradient"
      className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
      style={{
        // Static fallback; also what shows before the shader's first frame.
        background:
          "radial-gradient(60% 50% at 25% 30%, rgba(99,102,241,0.35), transparent 70%), radial-gradient(50% 45% at 80% 60%, rgba(168,85,247,0.28), transparent 70%), var(--bg-base)",
      }}
    >
      {!reduced && <canvas ref={canvasRef} className="h-full w-full opacity-70" />}
    </div>
  );
}
