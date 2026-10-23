/**
 * contrast.mjs
 * ============
 * WCAG 2.x contrast for every text/background pair the design tokens in
 * app/globals.css produce, in BOTH themes (the dark :root block and the
 * light :root[data-theme="light"] block).
 *
 *     node scripts/contrast.mjs        print the table; exit 1 on a failure
 *
 * tests/contrast.test.ts runs the same check in the unit suite, so a token
 * edited to a prettier but unreadable colour fails CI rather than a user.
 *
 * WHAT IS CHECKED
 * ---------------
 *   text   4.5:1  every text token (primary, secondary, muted, the accent
 *                 used for links, success / warning / danger) on every
 *                 surface it is drawn on: the page, an elevated panel, a
 *                 glass card, and a hovered / focused input on a card
 *   text   4.5:1  white button labels on both ends of the primary gradient
 *                 (--accent -> --violet)
 *   text   4.5:1  each status pill: its colour on its own tint, on a card
 *   ui     3:1    the focus ring / active border (--accent) on the page and
 *                 on a panel -- WCAG 1.4.11, non-text contrast
 *
 * Translucent tokens (--bg-card, --surface-*, the pill tints) are composited
 * over what they sit on before measuring, as the browser would paint them.
 * The ambient colour washes behind the page are ignored: at 8-16% opacity
 * they shift the result by well under the margin each pair keeps.
 *
 * WCAG's formula: contrast = (L1 + 0.05) / (L2 + 0.05), where L is the
 * relative luminance of the lighter and darker colour, from linearised sRGB.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const TEXT_MIN = 4.5;
export const UI_MIN = 3;

/** "#abc", "#aabbcc" or "rgba(r, g, b, a)" -> { r, g, b, a } (0-255, a 0-1). */
export function parseColor(value) {
  const v = value.trim().toLowerCase();
  if (v === "white" || v === "#fff") return { r: 255, g: 255, b: 255, a: 1 };
  let m = /^#([0-9a-f]{3})$/.exec(v);
  if (m) {
    const [r, g, b] = m[1].split("").map((c) => parseInt(c + c, 16));
    return { r, g, b, a: 1 };
  }
  m = /^#([0-9a-f]{6})$/.exec(v);
  if (m) {
    const n = parseInt(m[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(v);
  if (m) return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
  throw new Error(`Unsupported colour: ${value}`);
}

/** Paint `top` (possibly translucent) over an opaque `bottom`. */
export function composite(top, bottom) {
  const a = top.a;
  return {
    r: top.r * a + bottom.r * (1 - a),
    g: top.g * a + bottom.g * (1 - a),
    b: top.b * a + bottom.b * (1 - a),
    a: 1,
  };
}

/** WCAG relative luminance of an opaque colour. */
export function luminance({ r, g, b }) {
  const lin = (c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** Contrast ratio between two opaque colours, 1 to 21. */
export function contrastRatio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The custom properties declared directly in the block opening with `selector {`. */
export function tokenBlock(css, selector) {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`No ${selector} block in globals.css`);
  const body = css.slice(start, css.indexOf("}", start));
  const tokens = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) tokens[m[1]] = m[2].trim();
  return tokens;
}

/** Each `@utility pill-<name>`'s literal background and its colour token. */
export function pillStyles(css) {
  const pills = {};
  for (const m of css.matchAll(/@utility (pill-\w+) \{([^}]*)\}/g)) {
    const bg = /background:\s*([^;]+);/.exec(m[2]);
    const fg = /color:\s*var\((--[\w-]+)\)/.exec(m[2]);
    if (bg && fg) pills[m[1]] = { background: bg[1].trim(), color: fg[1] };
  }
  return pills;
}

/**
 * Every pair to check for one theme. `tokens` is the merged custom
 * properties for that theme. Returns [{ name, fg, bg, min, ratio }].
 */
export function themePairs(tokens, pills) {
  const c = (name) => {
    if (!(name in tokens)) throw new Error(`Missing token ${name}`);
    return parseColor(tokens[name]);
  };
  const base = c("--bg-base");
  const surfaces = {
    "bg-base": base,
    "bg-elevated": composite(c("--bg-elevated"), base),
    "bg-card": composite(c("--bg-card"), base),
  };
  surfaces["surface-2 on card"] = composite(c("--surface-2"), surfaces["bg-card"]);

  const textTokens = [
    "--text-primary",
    "--text-secondary",
    "--text-muted",
    "--accent-hover",
    "--success",
    "--warning",
    "--danger",
  ];
  const pairs = [];
  for (const fg of textTokens) {
    for (const [surface, bg] of Object.entries(surfaces)) {
      pairs.push({ name: `${fg} on ${surface}`, fg: c(fg), bg, min: TEXT_MIN });
    }
  }
  const white = parseColor("#ffffff");
  for (const end of ["--accent", "--violet"]) {
    pairs.push({ name: `white button label on ${end}`, fg: white, bg: c(end), min: TEXT_MIN });
  }
  for (const surface of ["bg-base", "bg-elevated", "bg-card"]) {
    pairs.push({ name: `--accent focus ring on ${surface}`, fg: c("--accent"), bg: surfaces[surface], min: UI_MIN });
  }
  for (const [pill, style] of Object.entries(pills)) {
    const tint = composite(parseColor(style.background), surfaces["bg-card"]);
    pairs.push({ name: `${pill} (${style.color} on its tint)`, fg: c(style.color), bg: tint, min: TEXT_MIN });
  }
  return pairs.map((p) => ({ ...p, ratio: contrastRatio(p.fg, p.bg) }));
}

/** Both themes' results, read from globals.css. */
export function checkGlobals(css) {
  const dark = tokenBlock(css, ":root");
  // The light block only overrides; anything it omits inherits the dark value.
  const light = { ...dark, ...tokenBlock(css, ':root[data-theme="light"]') };
  const pills = pillStyles(css);
  return { dark: themePairs(dark, pills), light: themePairs(light, pills) };
}

export function readGlobals() {
  const here = dirname(fileURLToPath(import.meta.url));
  return readFileSync(join(here, "..", "app", "globals.css"), "utf8");
}

// ---- CLI ---------------------------------------------------------------
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const results = checkGlobals(readGlobals());
  let failures = 0;
  for (const [theme, pairs] of Object.entries(results)) {
    console.log(`\n${theme} theme`);
    for (const p of pairs) {
      const ok = p.ratio >= p.min;
      if (!ok) failures += 1;
      console.log(`  ${ok ? "pass" : "FAIL"}  ${p.ratio.toFixed(2).padStart(5)} : 1  (min ${p.min})  ${p.name}`);
    }
  }
  console.log(failures ? `\n${failures} pair(s) below the minimum.` : "\nEvery pair meets WCAG AA.");
  process.exit(failures ? 1 : 0);
}
