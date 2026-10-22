/**
 * playwright.config.ts
 * ====================
 * End-to-end tests: a real browser against the real backend.
 *
 * WHAT THESE COVER THAT NOTHING ELSE DOES
 * ---------------------------------------
 * Every other test in this project mocks the boundary it is nearest to. The
 * Vitest suite mocks `lib/api`; the backend suite calls Python functions or
 * hits the ASGI app in-process. So the one thing neither can see is whether
 * the two halves actually agree.
 *
 * That matters here specifically, because the nine TypeScript interfaces in
 * `lib/api.ts` are hand-written to mirror `backend/app/schemas/`. Nothing
 * checks that they still match. Rename a field in a Pydantic schema and every
 * one of the 356 other tests still passes, while the running app quietly
 * renders `undefined`.
 *
 * These tests are the only thing in the repository that would notice.
 *
 * WHY THEY ARE NOT PART OF `npm test`
 * -----------------------------------
 * They need Postgres, Redis, the API and a Celery worker all running. That is
 * a reasonable thing to ask of CI and an unreasonable thing to ask of someone
 * who just wants to run the unit tests, so they live behind their own command
 * and their own CI job.
 *
 *     npm run test:e2e
 *
 * The `webServer` block below starts the Next.js dev server automatically, but
 * the BACKEND must already be up -- see docs/DEVELOPMENT.md (End-to-end tests).
 */

import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests-e2e",

  // Generous, because these wait on real work: a document is extracted,
  // chunked and embedded by a background worker before the assertions about
  // it can pass.
  timeout: 120_000,
  expect: { timeout: 15_000 },

  // Serial. These tests share one backend and one database, and a parallel
  // run would have them competing over the same account and project -- which
  // produces order-dependent failures that pass on a re-run and teach you
  // nothing.
  fullyParallel: false,
  workers: 1,

  // Never retry locally: a test that only passes on the second attempt is
  // telling you something, and retrying hides it. One retry in CI, where a
  // genuinely slow container start is a real and uninteresting cause.
  retries: process.env.CI ? 1 : 0,

  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],

  use: {
    baseURL: BASE_URL,
    // Artefacts only for failures. On a pass they are noise; on a failure the
    // trace is the difference between debugging and guessing.
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],

  // Starts `next dev` and waits for it. `reuseExistingServer` means a server
  // you already have running is used as-is rather than fought over for the
  // port.
  webServer: {
    command: "npm run dev",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
