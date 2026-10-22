/**
 * ProjectPage.test.tsx
 * ====================
 * WHAT THIS FILE TESTS
 * --------------------
 * `app/project/[id]/page.tsx` -- the page the three panels live on, and the
 * one that owns the document polling.
 *
 * WHY THE POLLING IS THE POINT
 * ----------------------------
 * Ingestion happens in a Celery worker and finishes whenever it finishes, so
 * the page asks every three seconds until nothing is pending. Two lines make
 * that safe, and BOTH fail silently:
 *
 *   1. The early return when nothing is pending. Without it an idle project
 *      polls forever, every three seconds, for a change that will never come.
 *
 *   2. The `clearInterval` cleanup. Without it every re-render stacks another
 *      timer, and the app escalates into hammering the API. The symptom --
 *      gradually increasing load with no obvious cause -- is genuinely
 *      unpleasant to track down, and it does not reproduce on a short visit.
 *
 * Neither shows up in a screenshot, in a type check, or in a manual test that
 * lasts less than a minute. That is exactly why they are worth pinning.
 *
 * Fake timers throughout, so three seconds costs nothing and the schedule is
 * under the test's control rather than the wall clock's.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProjectPage from "@/app/project/[id]/page";
import { pushMock } from "./setup";

const listDocuments = vi.fn();
const getProject = vi.fn();
const renameProject = vi.fn();
const deleteProject = vi.fn();
const renameDocument = vi.fn();
const deleteDocument = vi.fn();
const reindexDocument = vi.fn();
const getToken = vi.fn();
// The signed-in user; drives whether the upload zone is offered.
const getMe = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listDocuments: (...a: unknown[]) => listDocuments(...a),
    getProject: (...a: unknown[]) => getProject(...a),
    renameProject: (...a: unknown[]) => renameProject(...a),
    deleteProject: (...a: unknown[]) => deleteProject(...a),
    renameDocument: (...a: unknown[]) => renameDocument(...a),
    deleteDocument: (...a: unknown[]) => deleteDocument(...a),
    reindexDocument: (...a: unknown[]) => reindexDocument(...a),
    listProjectChatSessions: vi.fn().mockResolvedValue([]),
    createChatSession: vi.fn().mockResolvedValue({ id: "s1" }),
    search: vi.fn().mockResolvedValue({ results: [], took_seconds: 0 }),
    uploadDocuments: vi.fn().mockResolvedValue([]),
    getMe: (...a: unknown[]) => getMe(...a),
  },
  // UploadZone checks `instanceof ApiError`; a stand-in class suffices.
  ApiError: class ApiError extends Error {},
  getToken: () => getToken(),
  getErrorMessage: (e: unknown, fallback = "Something went wrong.") =>
    e instanceof Error && e.message ? e.message : fallback,
}));

vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

function doc(overrides: Record<string, unknown> = {}) {
  return {
    id: "doc-1",
    filename: "handbook.pdf",
    file_type: "pdf",
    file_size: 1024,
    status: "completed",
    meta_data: {},
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

/**
 * The route params, in the shape React's `use()` can read synchronously.
 *
 * Next.js 15 made dynamic route params async, so the page unwraps them with
 * `use()`. A test has to hand it a promise rather than a plain object -- which
 * the type system enforces, and which is how this file caught the change
 * during the Next 16 upgrade.
 *
 * WHY THE `status` AND `value` PROPERTIES
 * ---------------------------------------
 * `use()` on an ordinary pending promise SUSPENDS, and getting a suspended
 * render to resume inside a test turns out to be genuinely awkward: awaiting
 * the promise inside `act` is not enough, and it fails under real timers as
 * well as fake ones.
 *
 * React's own protocol offers a way out. A thenable already tagged
 * `status: "fulfilled"` is read straight off `value` with no suspension at
 * all -- which is exactly the state Next.js hands a page in production once
 * the router has resolved the segment. So this is not a trick to dodge
 * suspense; it is the resolved case, which is the one these tests are about.
 *
 * The promise must also be STABLE across renders: `use()` caches on identity,
 * so one built inline in the JSX is new every render and never settles.
 */
type SettledParams = Promise<{ id: string }> & {
  status: "fulfilled";
  value: { id: string };
};

function routeParams(id = "project-1"): SettledParams {
  const params = Promise.resolve({ id }) as SettledParams;
  params.status = "fulfilled";
  params.value = { id };
  return params;
}

/** Render the page with a stable, already-resolved params promise. */
function renderPage(paramsPromise: SettledParams = routeParams()) {
  return render(<ProjectPage params={paramsPromise} />);
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  getToken.mockReturnValue("a-valid-token");
  getMe.mockReset().mockResolvedValue({ id: "u1", email: "ed@example.com", role: "Manager", is_verified: true });
  listDocuments.mockReset().mockResolvedValue([doc()]);
  getProject.mockReset().mockResolvedValue({ id: "project-1", name: "HR Handbook", document_count: 1, ready_count: 1 });
  renameProject.mockReset().mockResolvedValue({ id: "project-1", name: "Policies" });
  deleteProject.mockReset().mockResolvedValue({});
  renameDocument.mockReset().mockResolvedValue({});
  deleteDocument.mockReset().mockResolvedValue({});
  reindexDocument.mockReset().mockResolvedValue({});
});

afterEach(() => {
  vi.useRealTimers();
});

/** Let pending promises settle without advancing the fake clock. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

/** Advance the fake clock inside act, so React processes the updates. */
async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
}

// ======================================================================
// THE POLLING LOOP
// ======================================================================

describe("document polling", () => {
  it("polls while a document is still processing", async () => {
    listDocuments.mockResolvedValue([doc({ status: "processing" })]);
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    const afterInitialLoad = listDocuments.mock.calls.length;

    await advance(3500);

    expect(listDocuments.mock.calls.length).toBeGreaterThan(afterInitialLoad);
  });

  it("polls while a document is still pending", async () => {
    // "pending" means the worker has not picked the job up yet -- the state a
    // document sits in forever when no worker is running.
    listDocuments.mockResolvedValue([doc({ status: "pending" })]);
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    const before = listDocuments.mock.calls.length;

    await advance(3500);

    expect(listDocuments.mock.calls.length).toBeGreaterThan(before);
  });

  it("STOPS polling once everything has settled", async () => {
    // The early return. Without it an idle project polls forever for a change
    // that will never come -- constant background load for nothing.
    listDocuments.mockResolvedValue([doc({ status: "completed" })]);
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    await settle();
    const afterSettling = listDocuments.mock.calls.length;

    await advance(15000); // five polling intervals

    expect(listDocuments.mock.calls.length).toBe(afterSettling);
  });

  it("does not poll when a document has failed", async () => {
    // "failed" is a terminal state. Polling it forever would never see a
    // change, because nothing is going to retry on its own.
    listDocuments.mockResolvedValue([doc({ status: "failed" })]);
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    await settle();
    const before = listDocuments.mock.calls.length;

    await advance(15000);

    expect(listDocuments.mock.calls.length).toBe(before);
  });

  it("does not poll an empty project", async () => {
    listDocuments.mockResolvedValue([]);
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    await settle();
    const before = listDocuments.mock.calls.length;

    await advance(15000);

    expect(listDocuments.mock.calls.length).toBe(before);
  });

  it("stops polling after the last document completes", async () => {
    // The transition that matters: the page must notice work has finished and
    // wind itself down, not keep asking.
    listDocuments.mockResolvedValue([doc({ status: "processing" })]);
    renderPage();
    await waitFor(() => expect(listDocuments).toHaveBeenCalled());

    // The worker finishes.
    listDocuments.mockResolvedValue([doc({ status: "completed" })]);
    await advance(3500);
    await settle();

    const afterCompletion = listDocuments.mock.calls.length;
    await advance(15000);

    expect(listDocuments.mock.calls.length).toBe(afterCompletion);
  });

  it("clears its timer on unmount", async () => {
    // The cleanup function. Without it the interval survives the component,
    // and the app quietly escalates into hammering the API with requests
    // nothing is listening for.
    listDocuments.mockResolvedValue([doc({ status: "processing" })]);
    const { unmount } = renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());

    unmount();
    const afterUnmount = listDocuments.mock.calls.length;

    await advance(15000);

    expect(listDocuments.mock.calls.length).toBe(afterUnmount);
  });

  it("does not stack timers across re-renders", async () => {
    // The other half of the same bug. The polling effect depends on
    // `documents`, which changes on every poll -- so the effect re-runs
    // constantly, and without cleanup each run would leave its timer behind.
    // Request volume would then grow without bound.
    listDocuments.mockResolvedValue([doc({ status: "processing" })]);
    renderPage();
    await waitFor(() => expect(listDocuments).toHaveBeenCalled());

    const before = listDocuments.mock.calls.length;
    await advance(3500);
    const afterOneInterval = listDocuments.mock.calls.length - before;

    const beforeSecond = listDocuments.mock.calls.length;
    await advance(3500);
    const afterAnother = listDocuments.mock.calls.length - beforeSecond;

    // Roughly constant per interval, not doubling. Compared with slack rather
    // than exact equality, because a poll's own state update can legitimately
    // schedule one extra tick.
    expect(afterAnother).toBeLessThanOrEqual(afterOneInterval + 1);
  });
});

// ======================================================================
// The auth guard
// ======================================================================

describe("the auth guard", () => {
  it("redirects a signed-out visitor to /login", async () => {
    getToken.mockReturnValue(null);
    renderPage();

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
  });

  it("does not load documents when signed out", async () => {
    // The redirect returns early. Fetching anyway would produce a 401 and a
    // pointless request on every page load.
    getToken.mockReturnValue(null);
    renderPage();

    await settle();
    expect(listDocuments).not.toHaveBeenCalled();
  });

  it("does not redirect a signed-in visitor", async () => {
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    expect(pushMock).not.toHaveBeenCalledWith("/login");
  });
});

// ======================================================================
// Loading the page
// ======================================================================

describe("loading", () => {
  it("scopes the document list to this project", async () => {
    renderPage(routeParams("project-42"));

    await waitFor(() => expect(listDocuments).toHaveBeenCalledWith("project-42"));
  });

  it("shows the project's name", async () => {
    renderPage();

    expect(await screen.findByText("HR Handbook")).toBeInTheDocument();
  });

  it("survives a failure to load the project name", async () => {
    // A missing heading is cosmetic. The documents are the page's purpose and
    // must still arrive.
    getProject.mockRejectedValue(new Error("Could not load the project"));
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    expect(await screen.findByText(/handbook\.pdf/)).toBeInTheDocument();
  });

  it("survives a transient failure to load documents", async () => {
    // Deliberately swallowed: the poll will try again shortly, and an error
    // banner that clears itself three seconds later is worse than no banner.
    listDocuments.mockRejectedValueOnce(new Error("Network blip"));
    renderPage();

    await settle();
    expect(screen.queryByText(/network blip/i)).not.toBeInTheDocument();
  });

  it("lists the documents", async () => {
    listDocuments.mockResolvedValue([
      doc({ id: "d1", filename: "first.pdf" }),
      doc({ id: "d2", filename: "second.docx" }),
    ]);
    renderPage();

    expect(await screen.findByText(/first\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/second\.docx/)).toBeInTheDocument();
  });
});

// ======================================================================
// Why a document failed, or is being retried
// ======================================================================

describe("failure reasons", () => {
  it("shows the backend's error_message for a failed document", async () => {
    listDocuments.mockResolvedValue([
      doc({
        status: "failed",
        error_message: "Rejected by the malware scan: Eicar-Signature",
        meta_data: { error: "Rejected by the malware scan: Eicar-Signature" },
      }),
    ]);
    renderPage();

    expect(
      await screen.findByText("Rejected by the malware scan: Eicar-Signature")
    ).toBeInTheDocument();
  });

  it("falls back to meta_data.error from an older backend", async () => {
    // Before `error_message` existed the reason lived only in meta_data.
    listDocuments.mockResolvedValue([
      doc({ status: "failed", meta_data: { error: "Unsupported file type: xyz" } }),
    ]);
    renderPage();

    expect(await screen.findByText("Unsupported file type: xyz")).toBeInTheDocument();
  });

  it("explains a pending document that is waiting to be retried", async () => {
    // A transient failure puts the document back on the queue. Without this
    // note it would just sit on "Queued" with no hint of why.
    listDocuments.mockResolvedValue([
      doc({ status: "pending", error_message: "embedding API unreachable", attempts: 1 }),
    ]);
    renderPage();

    expect(
      await screen.findByText(/Retrying after a temporary problem: embedding API unreachable/)
    ).toBeInTheDocument();
  });

  it("says nothing extra about an ordinary queued document", async () => {
    listDocuments.mockResolvedValue([doc({ status: "pending", error_message: null })]);
    renderPage();

    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    await settle();
    expect(screen.queryByText(/Retrying/)).not.toBeInTheDocument();
  });
});

// ======================================================================
// Role gating
// ======================================================================
// Uploading needs the editor role. A viewer is told so instead of being
// offered a drop zone that can only answer 403.

describe("role gating", () => {
  it("offers the upload zone to an editor", async () => {
    const { container } = renderPage();
    await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy());
  });

  it("hides the upload zone from a viewer and says why", async () => {
    getMe.mockResolvedValue({ id: "u2", email: "v@example.com", role: "Employee", is_verified: true });
    const { container } = renderPage();

    expect(await screen.findByText(/view-only access/i)).toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });
});

// ======================================================================
// Managing the project and its documents (editor+)
// ======================================================================

describe("managing documents", () => {
  it("offers no document actions to a viewer", async () => {
    getMe.mockResolvedValue({ id: "u2", email: "v@example.com", role: "Employee", is_verified: true });
    renderPage();

    await screen.findByText(/view-only access/i);
    expect(screen.queryByRole("button", { name: /delete handbook\.pdf/i })).not.toBeInTheDocument();
  });

  it("deletes a document only after a second click, then reloads", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /delete handbook\.pdf/i }));
    expect(deleteDocument).not.toHaveBeenCalled();

    const before = listDocuments.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /^delete\?$/i }));

    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith("doc-1"));
    await waitFor(() => expect(listDocuments.mock.calls.length).toBeGreaterThan(before));
  });

  it("re-indexes a document", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /re-index handbook\.pdf/i }));
    await waitFor(() => expect(reindexDocument).toHaveBeenCalledWith("doc-1"));
  });

  it("does not offer re-indexing while a document is still processing", async () => {
    listDocuments.mockResolvedValue([doc({ status: "processing" })]);
    renderPage();
    expect(await screen.findByRole("button", { name: /re-index handbook\.pdf/i })).toBeDisabled();
  });

  it("renames a document inline", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /rename handbook\.pdf/i }));
    const box = screen.getByRole("textbox", { name: /document name/i });
    fireEvent.change(box, { target: { value: "Staff handbook.pdf" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(renameDocument).toHaveBeenCalledWith("doc-1", "Staff handbook.pdf"));
  });

  it("shows why an action failed", async () => {
    deleteDocument.mockRejectedValue(new Error("This action requires the Manager role or higher."));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /delete handbook\.pdf/i }));
    fireEvent.click(screen.getByRole("button", { name: /^delete\?$/i }));

    expect(await screen.findByText(/requires the Manager role/)).toBeInTheDocument();
  });
});

describe("managing the project", () => {
  it("renames the project", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /rename project/i }));
    const box = screen.getByRole("textbox", { name: /project name/i });
    fireEvent.change(box, { target: { value: "Policies" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(renameProject).toHaveBeenCalledWith("project-1", "Policies"));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Policies"));
  });

  it("deletes the project after confirming, then returns to the dashboard", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /delete project/i }));
    expect(deleteProject).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /delete the project, its documents/i }));

    await waitFor(() => expect(deleteProject).toHaveBeenCalledWith("project-1"));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
  });
});
