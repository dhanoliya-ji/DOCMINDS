/**
 * contrast.test.ts
 * ================
 * Every text / background pair the design tokens in app/globals.css produce
 * meets WCAG AA in both themes (scripts/contrast.mjs does the measuring), and
 * the measuring itself agrees with known reference ratios.
 */

import { describe, expect, it } from "vitest";

import { checkGlobals, composite, contrastRatio, parseColor, readGlobals } from "../scripts/contrast.mjs";

describe("contrast maths", () => {
  it("matches WCAG's reference values", () => {
    expect(contrastRatio(parseColor("#000"), parseColor("#fff"))).toBeCloseTo(21, 5);
    expect(contrastRatio(parseColor("#777777"), parseColor("#ffffff"))).toBeCloseTo(4.48, 2);
  });

  it("composites a translucent colour over its background", () => {
    const half = composite(parseColor("rgba(255, 255, 255, 0.5)"), parseColor("#000000"));
    expect(Math.round(half.r)).toBe(128);
  });
});

describe("design tokens", () => {
  const results = checkGlobals(readGlobals()) as Record<string, { name: string; ratio: number; min: number }[]>;

  for (const theme of ["dark", "light"]) {
    it(`meet WCAG AA in the ${theme} theme`, () => {
      const failing = results[theme]
        .filter((p) => p.ratio < p.min)
        .map((p) => `${p.name}: ${p.ratio.toFixed(2)} < ${p.min}`);
      expect(failing).toEqual([]);
      expect(results[theme].length).toBeGreaterThan(30);
    });
  }
});
