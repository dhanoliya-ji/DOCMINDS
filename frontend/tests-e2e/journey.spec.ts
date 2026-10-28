// The whole product, once, in a real browser against the real backend.

import { expect, test, type Page } from "@playwright/test";

const RUN = Date.now().toString(36);
const EMAIL = `e2e-${RUN}@example.com`;
const PASSWORD = "an-e2e-test-password";
const ORG = `E2E Org ${RUN}`;
const PROJECT = `E2E Project ${RUN}`;

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
    page = await browser.newPage();

    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
  });

  test.afterAll(async () => {
    await page?.close();
  });

  test("the backend is reachable", async ({ request }) => {
    const response = await request.get("http://localhost:8000/api/v1/health");

    expect(
      response.ok(),
      "The backend is not answering on :8000. Start it first — see docs/DEVELOPMENT.md (End-to-end tests)."
    ).toBeTruthy();

    const health = await response.json();

    expect(
      health.llm?.provider,
      "run the backend with LLM_PROVIDER=mock for the e2e suite"
    ).toBe("mock");

    expect(
      health.embeddings?.provider,
      "run the backend with EMBEDDING_PROVIDER=local — mock embeddings cannot match a paraphrase, so the search step would fail"
    ).not.toBe("mock");
  });

  test("a new user can sign up", async () => {
    await page.goto("/login");

    await page.getByRole("button", { name: "Create one" }).click();

    await page.getByPlaceholder("Organization name").fill(ORG);
    await page.getByPlaceholder("you@company.com").fill(EMAIL);
    await page.getByPlaceholder("Password").fill(PASSWORD);

    await page.getByRole("button", { name: /Create workspace/i }).click();

    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 });
  });

  test("the token was stored", async () => {
    const token = await page.evaluate(() =>
      Object.keys(localStorage)
        .map((k) => localStorage.getItem(k))
        .find((v) => v && v.length > 20)
    );

    expect(token, "no token in localStorage after signup").toBeTruthy();
    authToken = token as string;
  });

  test("the API's embedding model is warm", async ({ request }) => {
    const response = await request.post("http://localhost:8000/api/v1/search/", {
      headers: { Authorization: `Bearer ${authToken}` },
      data: { query: "warming the embedding model" },
      timeout: 180_000,
    });

    expect(response.status(), "the search endpoint did not respond").toBeLessThan(500);
  });

  test("a project can be created", async () => {
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
    await expect(page.getByRole("button", { name: /Documents/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Ask AI/i })).toBeVisible();
  });

  test("a document can be uploaded", async () => {
    await page.setInputFiles('input[type="file"]', {
      name: DOC_NAME,
      mimeType: "text/plain",
      buffer: Buffer.from(DOC_TEXT, "utf-8"),
    });

    await expect(page.getByText(DOC_NAME).first()).toBeVisible({ timeout: 30_000 });
  });

  test("the worker processes it to completion", async () => {
    await expect(
      page.getByText("Ready", { exact: true }).first(),
      "The document never reached 'Ready'. Is the Celery worker running?"
    ).toBeVisible({ timeout: 90_000 });

    await expect(page.getByText(/1 ready to search/i)).toBeVisible();
  });

  test("search finds the document's own text", async () => {
    await page.getByRole("button", { name: /Search/i }).first().click();

    const box = page.getByPlaceholder(/Search by meaning/i);
    await box.fill("Employees accrue 1.75 days of paid leave per calendar month.");
    await box.press("Enter");

    await expect(page.getByText(DOC_NAME).first()).toBeVisible({ timeout: 30_000 });
  });

  test("a search result carries a similarity score", async () => {
    await expect(page.getByText(/\d+%\s*match/i).first()).toBeVisible({
      timeout: 20_000,
    });
  });

  test("the retrieved passage is the one that was uploaded", async () => {
    await expect(page.getByText(/1\.75 days of paid leave/).first()).toBeVisible({
      timeout: 20_000,
    });
  });

  test("chat answers from the document", async () => {
    await page.getByRole("button", { name: /Ask AI/i }).click();

    const input = page.getByPlaceholder(/Ask a question/i);
    await input.fill("How much paid leave do employees accrue?");
    await input.press("Enter");

    await expect(
      page.getByText("How much paid leave do employees accrue?").first()
    ).toBeVisible({ timeout: 20_000 });

    await expect(page.getByText(/source/i).first()).toBeVisible({ timeout: 60_000 });
  });

  test("the answer cites the uploaded document", async () => {
    await page.getByText(/source/i).first().click();

    await expect(page.getByText(DOC_NAME).first()).toBeVisible({ timeout: 20_000 });
  });

  test("no console errors along the journey", async () => {
    const real = consoleErrors.filter(
      (e) =>
        !/favicon|extension|DevTools|Download the React DevTools|hydrat/i.test(e)
    );

    expect(real, `console errors during the run:\n${real.join("\n")}`).toHaveLength(0);
  });
});
