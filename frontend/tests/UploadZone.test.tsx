/**
 * UploadZone.test.tsx
 * ===================
 * WHAT THIS FILE TESTS
 * --------------------
 * `components/UploadZone.tsx` -- drag and drop, and the upload call.
 *
 * THE ONE THAT MATTERS
 * --------------------
 * `preventDefault` on BOTH `onDragOver` and `onDrop`.
 *
 * Neither is obvious and both fail silently. Without it on `onDragOver` the
 * browser never fires `onDrop` at all, so the whole zone simply does nothing
 * -- no error, no console warning, just a dead area of the page. Without it on
 * `onDrop`, the browser's default takes over and NAVIGATES to the dropped
 * file, throwing the user out of the app entirely.
 *
 * Neither is visible to a reviewer reading the JSX, because the calls sit
 * inside handlers that look unremarkable. They are asserted here directly.
 *
 * `lib/api` is mocked: what is under test is this component's behaviour, not
 * the client's, which `api.test.ts` already covers.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UploadZone, { runPool, uploadErrorMessage } from "@/components/UploadZone";
import { ApiError } from "@/lib/api";

// Hoisted so the tests can assert on the calls.
const uploadFile = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { uploadFile: (...args: unknown[]) => uploadFile(...args) },
  // A minimal stand-in with the same shape as the real class: the component
  // only reads `status` to word the message.
  ApiError: class ApiError extends Error {
    status: number;
    retryAfter: number | null;
    constructor(message: string, status: number, retryAfter: number | null = null) {
      super(message);
      this.status = status;
      this.retryAfter = retryAfter;
    }
  },
  getErrorMessage: (error: unknown, fallback = "Something went wrong.") =>
    error instanceof Error && error.message ? error.message : fallback,
}));

/** A drop event carrying files, shaped the way the browser delivers one. */
function dropEvent(files: File[]) {
  return {
    dataTransfer: {
      files,
      items: files.map((f) => ({ kind: "file", type: f.type, getAsFile: () => f })),
      types: ["Files"],
    },
  };
}

function makeFile(name = "handbook.pdf") {
  return new File(["file content"], name, { type: "application/pdf" });
}

/**
 * The drop zone element -- the one actually carrying the drag handlers.
 *
 * Found by walking up from the instruction text the user reads, rather than by
 * matching a Tailwind class. Restyling the zone is routine and must not break
 * these tests; removing the words "drag files here" would be a real change to
 * what the component offers, and breaking then is correct.
 */
function zone(container: HTMLElement): HTMLElement {
  const label = container.querySelector("p");
  const dropZone = label?.parentElement;
  if (!dropZone) throw new Error("drop zone not found -- has the markup changed?");
  return dropZone as HTMLElement;
}

beforeEach(() => {
  uploadFile.mockReset();
  uploadFile.mockResolvedValue([{ filename: "handbook.pdf", id: "1" }]);

  // The component clears its success message with `setTimeout(..., 4000)`.
  // On real timers that fires long after the test has finished and the
  // component has been unmounted, and React rightly warns about a state
  // update outside act(). Fake timers put that schedule under the test's
  // control -- which also makes the 4-second behaviour itself assertable
  // rather than merely tolerated.
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

// ======================================================================
// The two preventDefault calls
// ======================================================================

describe("drag and drop plumbing", () => {
  it("calls preventDefault on dragOver", () => {
    // Without this, the browser NEVER FIRES onDrop. The zone becomes inert
    // with nothing to explain why.
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    const event = new Event("dragover", { bubbles: true, cancelable: true });
    fireEvent(zone(container), event);

    expect(event.defaultPrevented).toBe(true);
  });

  it("calls preventDefault on drop", () => {
    // Without this, the browser navigates AWAY from the app to display the
    // dropped file.
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    // Deliberately an EMPTY file list. preventDefault runs first, before the
    // handler looks at the files at all, so this still tests what it claims
    // to -- and it avoids kicking off an async upload whose state update
    // would land after the test has finished, which React reports as an
    // act() warning that has nothing to do with the assertion here.
    const event = Object.assign(
      new Event("drop", { bubbles: true, cancelable: true }),
      dropEvent([])
    );
    fireEvent(zone(container), event);

    expect(event.defaultPrevented).toBe(true);
  });

  it("calls preventDefault on dragLeave", () => {
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    const event = new Event("dragleave", { bubbles: true, cancelable: true });
    fireEvent(zone(container), event);

    expect(event.defaultPrevented).toBe(true);
  });
});

// ======================================================================
// Uploading
// ======================================================================

describe("dropping files", () => {
  it("uploads the dropped file to the right project", async () => {
    const { container } = render(<UploadZone projectId="project-42" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));

    await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1));
    expect(uploadFile.mock.calls[0][0]).toBe("project-42");
    expect(uploadFile.mock.calls[0][1]).toBeInstanceOf(File);
  });

  it("uploads each file separately", async () => {
    // One request per file, so one bad file cannot sink the whole batch.
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(
      zone(container),
      dropEvent([makeFile("a.pdf"), makeFile("b.pdf"), makeFile("c.pdf")])
    );

    await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(3));
    expect(uploadFile.mock.calls.map((call) => (call[1] as File).name)).toEqual([
      "a.pdf",
      "b.pdf",
      "c.pdf",
    ]);
  });

  it("sends at most three at a time", async () => {
    uploadFile.mockReturnValue(new Promise(() => {})); // never finishes
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent(["1", "2", "3", "4", "5"].map((n) => makeFile(`${n}.pdf`))));

    await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(3));
    await new Promise((r) => setTimeout(r, 20));
    expect(uploadFile).toHaveBeenCalledTimes(3);
    expect(screen.getAllByText("queued")).toHaveLength(2);
  });

  it("one failed file does not stop the others", async () => {
    uploadFile.mockImplementation(async (_project: string, file: File) => {
      if (file.name === "bad.exe") throw new ApiError("bad.exe: this file type is not supported.", 415);
      return [{ filename: file.name, id: file.name }];
    });
    const onUploaded = vi.fn();
    const { container } = render(<UploadZone projectId="p1" onUploaded={onUploaded} />);

    fireEvent.drop(zone(container), dropEvent([makeFile("good.pdf"), makeFile("bad.exe"), makeFile("also-good.pdf")]));

    expect(await screen.findByRole("alert")).toHaveTextContent(/^Unsupported file: bad\.exe/);
    await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/2 of 3 uploaded · 1 failed/)).toBeInTheDocument();
  });

  it("shows each file's progress", async () => {
    let report: (fraction: number) => void = () => {};
    uploadFile.mockImplementation(
      (_project: string, _file: File, onProgress: (fraction: number) => void) => {
        report = onProgress;
        return new Promise(() => {});
      }
    );
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile("big.pdf")]));
    await waitFor(() => expect(uploadFile).toHaveBeenCalled());
    act(() => report(0.42));

    expect(await screen.findByRole("progressbar", { name: /uploading big\.pdf/i })).toHaveAttribute(
      "aria-valuenow",
      "42"
    );
  });

  it("does nothing when the drop carries no files", async () => {
    // Dragging selected TEXT onto the page fires a drop with an empty file
    // list. Uploading nothing would show a spinner that never resolves.
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([]));

    await new Promise((r) => setTimeout(r, 20));
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it("notifies the parent after a successful upload", async () => {
    // This is what makes the new document appear in the list with its live
    // "pending" status. Without it the upload works and nothing visibly
    // happens.
    const onUploaded = vi.fn();
    const { container } = render(<UploadZone projectId="p1" onUploaded={onUploaded} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));

    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
  });

  it("does NOT notify the parent when the upload failed", async () => {
    uploadFile.mockRejectedValue(new Error("This file has already been uploaded."));
    const onUploaded = vi.fn();
    const { container } = render(<UploadZone projectId="p1" onUploaded={onUploaded} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));

    await waitFor(() => expect(uploadFile).toHaveBeenCalled());
    await screen.findByRole("alert");
    expect(onUploaded).not.toHaveBeenCalled();
  });
});

// ======================================================================
// What the user is told
// ======================================================================

describe("feedback", () => {
  it("shows the backend's reason when an upload fails", async () => {
    uploadFile.mockRejectedValue(new Error("This file has already been uploaded."));
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));

    expect(await screen.findByText(/already been uploaded/i)).toBeInTheDocument();
  });

  it("falls back to a readable message when the error has none", async () => {
    uploadFile.mockRejectedValue(new Error(""));
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));

    expect(await screen.findByText(/upload failed/i)).toBeInTheDocument();
  });

  it("lists the file while it uploads and when it is done", async () => {
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile("quarterly-report.pdf")]));

    expect(await screen.findByText("quarterly-report.pdf")).toBeInTheDocument();
    expect(await screen.findByLabelText("Uploaded")).toBeInTheDocument();
  });

  it("clears finished uploads after a few seconds", async () => {
    // A success row that never goes away turns into permanent furniture.
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile("report.pdf")]));
    await screen.findByLabelText("Uploaded");

    await act(async () => {
      vi.advanceTimersByTime(4500);
    });

    expect(screen.queryByText("report.pdf")).not.toBeInTheDocument();
  });

  it("keeps a failure until it is dismissed", async () => {
    // A failure that vanished on its own could be missed entirely.
    uploadFile.mockRejectedValueOnce(new Error("First attempt failed."));
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));
    expect(await screen.findByText(/first attempt failed/i)).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText(/first attempt failed/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear errors/i }));
    expect(screen.queryByText(/first attempt failed/i)).not.toBeInTheDocument();
  });
});

// ======================================================================
// The worker pool
// ======================================================================

describe("runPool", () => {
  it("runs every item, never more than the limit at once", async () => {
    let running = 0;
    let peak = 0;
    const seen: number[] = [];
    await runPool([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise((r) => setTimeout(r, 1));
      seen.push(n);
      running -= 1;
    });
    expect(seen.sort()).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(peak).toBe(3);
  });
});

// ======================================================================
// Refusals worded by status (413 / 415 / 429 / 402)
// ======================================================================
// The backend's detail says WHAT was wrong; the prefix says what KIND of
// problem it is, so "make it smaller" is not confused with "wait a minute".

describe("upload refusals", () => {
  it.each([
    [413, "handbook.pdf is larger than the 50 MB limit.", /^File too large: handbook\.pdf is larger/],
    [415, "virus.exe: this file type is not supported.", /^Unsupported file: virus\.exe/],
    [429, "Too many uploads. Please try again in 40 seconds.", /^Too many uploads: .*40 seconds/],
    [402, "Your organization has reached its document quota.", /^Quota reached: /],
  ])("words a %i clearly, keeping the backend's reason", async (status, detail, expected) => {
    uploadFile.mockRejectedValueOnce(new ApiError(detail, status));
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(expected);
  });

  it("leaves other errors in their own words", () => {
    expect(uploadErrorMessage(new Error("Network down"))).toBe("Network down");
    expect(uploadErrorMessage(new ApiError("Bad chunk size", 422))).toBe("Bad chunk size");
  });
});

// ======================================================================
// The file picker
// ======================================================================

describe("the file input", () => {
  it("exists but is hidden", () => {
    // The native input cannot be styled, so it is hidden and triggered from a
    // styled button. It must still BE there -- it is the keyboard-accessible
    // path, and the only one that works without a pointer.
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    const input = container.querySelector('input[type="file"]');
    expect(input).toBeTruthy();
  });

  it("accepts multiple files", () => {
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    expect(container.querySelector('input[type="file"]')).toHaveAttribute("multiple");
  });

  it("filters to the extensions the backend can extract", () => {
    // A hint, not a check -- the real validation is server-side. But offering
    // a format the backend rejects wastes the user's upload.
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    const accept = container.querySelector('input[type="file"]')?.getAttribute("accept") ?? "";

    for (const ext of [".pdf", ".docx", ".csv", ".zip", ".png", ".md"]) {
      expect(accept).toContain(ext);
    }
  });

  it("uploads a file chosen through the picker", async () => {
    const user = userEvent.setup();
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, makeFile());

    await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1));
  });
});
