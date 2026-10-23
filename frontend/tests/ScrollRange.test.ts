/**
 * scrollRange keeps scroll-linked input ranges inside 0..1 and increasing.
 * Without it Chrome threw "Offsets must be monotonically non-decreasing"
 * and the live landing page crashed on load.
 */
import { describe, expect, it } from "vitest";

import { scrollRange } from "@/components/landing/PipelineScroll";

const valid = (r: number[]) =>
  r.every((v, i) => v >= 0 && v <= 1 && (i === 0 || v > r[i - 1]));

describe("scrollRange", () => {
  it("clamps the first stage's negative range", () => {
    expect(valid(scrollRange([-1 / 3, -0.1167, 0.1167, 0.2667]))).toBe(true);
  });

  it("clamps the last stage's range past 1", () => {
    expect(valid(scrollRange([2 / 3, 0.88, 1.12, 1.27]))).toBe(true);
  });

  it("leaves an ordinary range unchanged", () => {
    expect(scrollRange([0.2, 0.5, 0.8])).toEqual([0.2, 0.5, 0.8]);
  });
});
