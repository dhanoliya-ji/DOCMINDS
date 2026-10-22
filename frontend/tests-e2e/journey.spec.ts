/**
 * journey.spec.ts
 * ===============
 * The whole product, once, in a real browser against the real backend.
 *
 * Sign up -> create a project -> upload a document -> wait for the worker to
 * finish it -> search it -> ask a question about it.
 *
 * WHY ONE SERIAL JOURNEY RATHER THAN SIX INDEPENDENT TESTS
 * -------------------------------------------------------
 * Normally sharing state between tests is a mistake. Here the state IS the
 * subject: you cannot search a document you have not uploaded, and you cannot
 * upload one without a project. Splitting these up would mean either redoing
 * the whole setup six times against a pipeline that takes real seconds, or
 * faking the setup -- which is exactly what these tests exist not to do.
 *
 * So it is one `describe.serial`: each step depends on the last, and the first
 * failure stops the rest rather than reporting five confusing consequences of
 * one cause.
 *
 * WHAT THIS CATCHES THAT 356 OTHER TESTS CANNOT
 * ---------------------------------------------
 * Schema drift. `lib/api.ts` hand-writes nine interfaces mirroring
 * `backend/app/schemas/`, and nothing checks they still match. Rename
 * `filename` to `file_name` in a Pydantic model and every unit test on both
 * sides still passes, because each mocks the other. Only a real request across
 * the real boundary notices -- here, as a filename that renders as nothing.
 *
 * A NOTE ON THE SELECTORS
 * -----------------------
 * Every locator below was taken from the actual markup, not guessed. The first
 * draft of this file guessed at `getByLabel(...)` and failed on all ten steps,
 * because these forms label their inputs with placeholders rather than
 * `<label>` elements. Worth knowing before "fixing" a selector here: read the
 * component first.
 *
 * REQUIREMENTS
 * ------------
 * Postgres, Redis, the API and a Celery worker, all running, with
 * LLM_PROVIDER=mock, OCR_PROVIDER=mock and EMBEDDING_PROVIDER=local (not mock;
 * see docs/DEVELOPMENT.md, End-to-end tests).
 */

import { expect, test, type Page } from "@playwright/test";

// A fresh identity per run. These write real rows to a real database, and
// reusing an email would collide with the previous run on the second
// execution -- a failure that looks like a bug in signup.
const RUN = Date.now().toString(36);
const EMAIL = `e2e-${RUN}@example.com`;
const PASSWORD = "an-e2e-test-password";
const ORG = `E2E Org ${RUN}`;
const PROJECT = `E2E Project ${RUN}`;

// Content with a distinctive, searchable fact in it. The mock embedding
// provider produces meaningless-but-deterministic vectors, so semantic
// similarity is not what is under test -- what matters is that this exact text
// survives extraction, chunking, storage and retrieval intact.
const DOC_TEXT = [
  "ACME Corporation Employee Handbook",
  "",
  "Section 4: Leave",
  "Employees accrue 1.75 days of paid leave per calendar month.",
  "Unused leave may be carried over into the following year, up to a maximum",
  "of ten days. Requests must be submitted at least fourteen days in advance.",
  "",
  "Section 5: Equipment",
  "The company provides a laptop and a monitor to every employee.",
].join("\n");

const DOC_NAME = `handbook-${RUN}.txt`;

test.describe.serial("the full DocMinds journey", () => {
  let page: Page;
  let authToken = "";
  const consoleErrors: string[] = [];

  test.beforeAll(async ({ browser }) => {
    // One page for the whole journey, so the token in localStorage survives
    // from signup through to the final question.
    page = await browser.newPage();

    // Collected across every step rather than asserted per step. A failed
    // request or a React warning logs here without failing anything, and
    // those are exactly the problems that reach production because nobody
    // had the console open. Asserted at the end.
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
  });

  test.afterAll(async () => {
    await page?.close();
  });

  // ==================================================================
  test("the backend is reachable", async ({ request }) => {
    // Checked first and separately, so a stack that is not running fails here
    // with an obvious message rather than as a mystifying timeout hunting for
    // a button five steps later.
    const response = await request.get("http://localhost:8000/api/v1/health");

    expect(
      response.ok(),
      "The backend is not answering on :8000. Start it first — see docs/DEVELOPMENT.md (End-to-end tests)."
    ).toBeTruthy();

    const health = await response.json();

    // The LLM stays mocked: no API key, and the answer's text is not what
    // these tests assert on.
    expect(
      health.llm?.provider,
      "run the backend with LLM_PROVIDER=mock for the e2e suite"
    ).toBe("mock");

    // But EMBEDDINGS must be real, and this is worth understanding before
    // "simplifying" it back to mock.
    //
    // The mock provider hashes the text it is given, so identical text gives
    // an identical vector and anything else gives an unrelated one. That is
    // perfect for unit tests and useless here: a whole short document becomes
    // ONE chunk, so searching for a sentence FROM it produces a vector
    // unrelated to the chunk's, a similarity below RETRIEVAL_MIN_SCORE, and
    // zero results.
    //
    // Diagnosed exactly that way while writing this file -- the stored chunk
    // was the full 109-character document, not the sentence being searched
    // for. Meaning-based retrieval is the product's central claim, and only a
    // real embedding model can demonstrate it.
    expect(
      health.embeddings?.provider,
      "run the backend with EMBEDDING_PROVIDER=local — mock embeddings cannot match a paraphrase, so the search step would fail"
    ).not.toBe("mock");
  });

  // ==================================================================
  test("a new user can sign up", async () => {
    await page.goto("/login");

    // "Create one" is the toggle from sign-in to sign-up.
    await page.getByRole("button", { name: "Create one" }).click();

    // These forms label their inputs with placeholders, not <label> elements.
    await page.getByPlaceholder("Organization name").fill(ORG);
    await page.getByPlaceholder("you@company.com").fill(EMAIL);
    await page.getByPlaceholder("Password").fill(PASSWORD);

    await page.getByRole("button", { name: /Create workspace/i }).click();

    // Landing on the dashboard proves the whole chain: the organisation and
    // user rows were created, a token came back, and the client stored it.
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 });
  });

  test("the token was stored", async () => {
    // The mechanism behind every request that follows. If this were empty,
    // the later steps would fail with 401s that look like permission bugs.
    const token = await page.evaluate(() =>
      Object.keys(localStorage)
        .map((k) => localStorage.getItem(k))
        .find((v) => v && v.length > 20)
    );

    expect(token, "no token in localStorage after signup").toBeTruthy();
    authToken = token as string;
  });

  test("the API's embedding model is warm", async ({ request }) => {
    // A deliberate, unasserted throwaway request, and the reason is a real
    // property of the system rather than test hygiene.
    //
    // The local embedding model is loaded LAZILY and PER PROCESS. The Celery
    // worker pays roughly 40 seconds for it on the first document -- visible
    // in that document's own `processing_seconds` -- and the API process pays
    // it again, separately, on its first search.
    //
    // Without this step that second load lands inside the search assertion,
    // whose timeout then has to cover a one-time model load as well as a
    // query. It was set to 45s and failed on exactly that boundary while this
    // file was being written: the pipeline had worked perfectly and the test
    // still went red.
    //
    // Paying it here instead means every timeout after this one measures what
    // it claims to measure.
    const response = await request.post("http://localhost:8000/api/v1/search/", {
      headers: { Authorization: `Bearer ${authToken}` },
      data: { query: "warming the embedding model" },
      timeout: 180_000,
    });

    // Any answer at all is enough -- an empty result set is the expected one,
    // since nothing has been uploaded yet.
    expect(response.status(), "the search endpoint did not respond").toBeLessThan(500);
  });

  // ==================================================================
  test("a project can be created", async () => {
    // A brand-new account has no projects, so the empty state's button is
    // what is on screen. Either label is accepted, because which one appears
    // depends on a state this test should not care about.
    await page
      .getByRole("button", { name: /New project|Create a project/i })
      .first()
      .click();

    await page.getByPlaceholder(/HR Policies/i).fill(PROJECT);
    await page.getByRole("button", { name: /^Create$/ }).click();

    await expect(page.getByText(PROJECT)).toBeVisible({ timeout: 20_000 });
  });

  test("the project opens", async () => {
    await page.getByText(PROJECT).first().click();

    await expect(page).toHaveURL(/\/project\//, { timeout: 20_000 });
    // The three tabs are how you know the project page rendered rather than
    // an error boundary. Note the third is "Ask AI", not "Chat".
    await expect(page.getByRole("button", { name: /Documents/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Ask AI/i })).toBeVisible();
  });

  // ==================================================================
  test("a document can be uploaded", async () => {
    // Set the file on the hidden input directly. Playwright can drive a real
    // OS drag-and-drop, but the input is the path a keyboard user takes and it
    // exercises the same upload call.
    await page.setInputFiles('input[type="file"]', {
      name: DOC_NAME,
      mimeType: "text/plain",
      buffer: Buffer.from(DOC_TEXT, "utf-8"),
    });

    // `.first()` because the name legitimately appears twice: once in the
    // upload confirmation ("Queued 1 file for processing") and once in the
    // document row. Playwright's strict mode fails on the ambiguity rather
    // than picking one silently, which is the right default -- but here both
    // matches mean the upload worked.
    await expect(page.getByText(DOC_NAME).first()).toBeVisible({ timeout: 30_000 });
  });

  test("the worker processes it to completion", async () => {
    // THE STEP THAT JUSTIFIES THE WHOLE FILE.
    //
    // Getting here means the API wrote the file and queued a job, Redis
    // carried it, a Celery worker picked it up, and all seven ingestion
    // stages ran -- extract, chunk, embed, store. Nothing else in this
    // repository verifies that chain end to end.
    //
    // It is also the most common setup failure: with no worker running this
    // sits on "pending" forever, so the message says so.
    //
    // The UI shows "Ready" rather than "completed" -- the status column's
    // value and its label are deliberately different things.
    await expect(
      page.getByText("Ready", { exact: true }).first(),
      "The document never reached 'Ready'. Is the Celery worker running?"
    ).toBeVisible({ timeout: 90_000 });

    // And the header's counter agrees, which is a second reading of the same
    // fact from a different query.
    await expect(page.getByText(/1 ready to search/i)).toBeVisible();
  });

  // ==================================================================
  test("search finds the document's own text", async () => {
    await page.getByRole("button", { name: /Search/i }).first().click();

    const box = page.getByPlaceholder(/Search by meaning/i);
    await box.fill("Employees accrue 1.75 days of paid leave per calendar month.");
    await box.press("Enter");

    // The filename appearing here is the assertion that catches schema drift.
    // It travelled from the Document row, through the retrieval query, into
    // the SearchResult schema, across JSON, into the hand-written TypeScript
    // interface, and onto the page. A rename anywhere on that path renders
    // nothing.
    // 30s is now a real budget for a query, because the one-time model load
    // was paid in the warm-up step above.
    await expect(page.getByText(DOC_NAME).first()).toBeVisible({ timeout: 30_000 });
  });

  test("a search result carries a similarity score", async () => {
    // Displayed as a percentage. Its presence proves the score survived the
    // round trip as a number rather than arriving as a string or undefined.
    await expect(page.getByText(/\d+%\s*match/i).first()).toBeVisible({
      timeout: 20_000,
    });
  });

  test("the retrieved passage is the one that was uploaded", async () => {
    // Not just "a result came back" -- the right text came back, unmangled by
    // extraction and chunking.
    await expect(page.getByText(/1\.75 days of paid leave/).first()).toBeVisible({
      timeout: 20_000,
    });
  });

  // ==================================================================
  test("chat answers from the document", async () => {
    await page.getByRole("button", { name: /Ask AI/i }).click();

    const input = page.getByPlaceholder(/Ask a question/i);
    await input.fill("How much paid leave do employees accrue?");
    await input.press("Enter");

    // The question appears immediately -- the optimistic append.
    await expect(
      page.getByText("How much paid leave do employees accrue?").first()
    ).toBeVisible({ timeout: 20_000 });

    // The mock LLM does not write a real answer, so this asserts that a reply
    // ARRIVED rather than what it says. The route exercised is the one that
    // matters: retrieve, build the numbered prompt, generate, save, return.
    // Its own citation count is the proof retrieval fed the prompt.
    await expect(page.getByText(/source/i).first()).toBeVisible({ timeout: 60_000 });
  });

  test("the answer cites the uploaded document", async () => {
    // The citation is the end of the longest chain in the product: the page
    // number threaded from the extractor's page marker through the chunker,
    // retrieval and the numbered prompt into the UI. Expanding it shows which
    // file the claim came from.
    await page.getByText(/source/i).first().click();

    await expect(page.getByText(DOC_NAME).first()).toBeVisible({ timeout: 20_000 });
  });

  // ==================================================================
  test("no console errors along the journey", async () => {
    // Filtered: a favicon 404, browser extension noise and React's own
    // devtools advertisement are not this application's problem.
    const real = consoleErrors.filter(
      (e) =>
        !/favicon|extension|DevTools|Download the React DevTools|hydrat/i.test(e)
    );

    expect(real, `console errors during the run:\n${real.join("\n")}`).toHaveLength(0);
  });
});
