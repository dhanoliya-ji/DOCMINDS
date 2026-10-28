// `documentProgress` and `describeProgress` in lib/progress.ts: the line under a large...
import { describe, expect, it } from "vitest";

import type { DocumentItem } from "@/lib/api";
import { describeProgress, documentProgress } from "@/lib/progress";

const withMeta = (meta: object) =>
  ({ meta_data: meta }) as unknown as Pick<DocumentItem, "meta_data">;

describe("documentProgress", () => {
  it("returns the progress object", () => {
    expect(
      documentProgress(
        withMeta({ progress: { stage: "embedding", embedded: 1, total: 2 } }),
      ),
    ).toEqual({
      stage: "embedding",
      embedded: 1,
      total: 2,
    });
  });

  it("is null for an older backend or no progress yet", () => {
    expect(documentProgress(withMeta({}))).toBeNull();
    expect(documentProgress(withMeta({ progress: "x" }))).toBeNull();
  });
});

describe("describeProgress", () => {
  const now = new Date("2026-10-23T12:00:00Z");

  it("counts pages while reading", () => {
    expect(
      describeProgress({
        stage: "extracting",
        pages_done: 40,
        pages_total: 1200,
      }),
    ).toBe("Reading page 40 of 1,200");
  });

  it("counts chunks while embedding", () => {
    expect(
      describeProgress({ stage: "embedding", embedded: 340, total: 1200 }),
    ).toBe("Embedding 340 of 1,200 chunks");
  });

  it("says a paused document resumes on its own, and roughly when", () => {
    const line = describeProgress(
      {
        stage: "paused",
        embedded: 900,
        total: 1200,
        resume_at: "2026-10-23T12:02:00Z",
      },
      now,
    );
    expect(line).toContain(
      "Paused for the embedding rate limit (about 2 minutes)",
    );
    expect(line).toContain("900 of 1,200 chunks done, resumes automatically");
  });

  it("speaks Hindi when asked, with the same numbers", () => {
    expect(
      describeProgress({ stage: "extracting", pages_done: 40, pages_total: 1200 }, now, "hi"),
    ).toBe("पृष्ठ 40 / 1,200 पढ़ा जा रहा है");
    expect(describeProgress({ stage: "embedding", embedded: 340, total: 1200 }, now, "hi")).toBe(
      "1,200 में से 340 खंड एम्बेड हुए",
    );
    const line = describeProgress(
      { stage: "paused", embedded: 900, total: 1200, resume_at: "2026-10-23T12:02:00Z" },
      now,
      "hi",
    );
    expect(line).toContain("(लगभग 2 मिनट)");
    expect(line).toContain("अपने आप फिर शुरू होगा");
  });

  it("says nothing once finished", () => {
    expect(describeProgress({ stage: "completed" })).toBeNull();
    expect(describeProgress(null)).toBeNull();
  });
});
