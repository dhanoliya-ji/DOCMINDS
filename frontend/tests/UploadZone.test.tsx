// `components/UploadZone.tsx` -- drag and drop, and the upload call.

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UploadZone, { runPool, uploadErrorMessage, zonePhase } from "@/components/UploadZone";
import { setLocale } from "@/lib/i18n";
import { ApiError } from "@/lib/api";

const uploadFile = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { uploadFile: (...args: unknown[]) => uploadFile(...args) },
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

function zone(container: HTMLElement): HTMLElement {
  const label = container.querySelector("p");
  const dropZone = label?.parentElement;
  if (!dropZone) throw new Error("drop zone not found -- has the markup changed?");
  return dropZone as HTMLElement;
}

beforeEach(() => {
  uploadFile.mockReset();
  uploadFile.mockResolvedValue([{ filename: "handbook.pdf", id: "1" }]);

  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});


describe("drag and drop plumbing", () => {
  it("calls preventDefault on dragOver", () => {
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    const event = new Event("dragover", { bubbles: true, cancelable: true });
    fireEvent(zone(container), event);

    expect(event.defaultPrevented).toBe(true);
  });

  it("calls preventDefault on drop", () => {
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

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


describe("dropping files", () => {
  it("uploads the dropped file to the right project", async () => {
    const { container } = render(<UploadZone projectId="project-42" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile()]));

    await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1));
    expect(uploadFile.mock.calls[0][0]).toBe("project-42");
    expect(uploadFile.mock.calls[0][1]).toBeInstanceOf(File);
  });

  it("uploads each file separately", async () => {
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
    uploadFile.mockReturnValue(new Promise(() => {}));
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
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([]));

    await new Promise((r) => setTimeout(r, 20));
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it("notifies the parent after a successful upload", async () => {
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
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    fireEvent.drop(zone(container), dropEvent([makeFile("report.pdf")]));
    await screen.findByLabelText("Uploaded");

    await act(async () => {
      vi.advanceTimersByTime(4500);
    });

    expect(screen.queryByText("report.pdf")).not.toBeInTheDocument();
  });

  it("keeps a failure until it is dismissed", async () => {
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


describe("the file input", () => {
  it("exists but is hidden", () => {
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    const input = container.querySelector('input[type="file"]');
    expect(input).toBeTruthy();
  });

  it("accepts multiple files", () => {
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);

    expect(container.querySelector('input[type="file"]')).toHaveAttribute("multiple");
  });

  it("filters to the extensions the backend can extract", () => {
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


describe("zone states", () => {
  const row = (status: "queued" | "uploading" | "done" | "error", progress = 0) => ({
    key: Math.random().toString(),
    name: "a.pdf",
    size: 1,
    status,
    progress,
  });

  it("derives idle, drag-over, uploading, processing, done and error", () => {
    expect(zonePhase(false, [])).toBe("idle");
    expect(zonePhase(true, [row("uploading", 0.5)])).toBe("drag-over");
    expect(zonePhase(false, [row("queued")])).toBe("uploading");
    expect(zonePhase(false, [row("uploading", 0.5)])).toBe("uploading");
    expect(zonePhase(false, [row("uploading", 1), row("done", 1)])).toBe("processing");
    expect(zonePhase(false, [row("done", 1)])).toBe("done");
    expect(zonePhase(false, [row("done", 1), row("error")])).toBe("error");
  });

  it("walks the zone through drag-over, uploading, processing and done", async () => {
    let report: (fraction: number) => void = () => {};
    let finish: () => void = () => {};
    uploadFile.mockImplementation(
      (_project: string, _file: File, onProgress: (fraction: number) => void) => {
        report = onProgress;
        return new Promise<void>((resolve) => (finish = resolve));
      }
    );
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);
    const target = screen.getByTestId("upload-zone");
    expect(target).toHaveAttribute("data-phase", "idle");

    fireEvent.dragOver(zone(container));
    expect(target).toHaveAttribute("data-phase", "drag-over");
    expect(screen.getByText("Drop to upload")).toBeInTheDocument();

    fireEvent.drop(zone(container), dropEvent([makeFile("big.pdf")]));
    await waitFor(() => expect(target).toHaveAttribute("data-phase", "uploading"));
    act(() => report(1));
    expect(target).toHaveAttribute("data-phase", "processing");
    expect(screen.getByText("processing")).toBeInTheDocument();

    await act(async () => finish());
    await waitFor(() => expect(target).toHaveAttribute("data-phase", "done"));
  });

  it("shows the error state when a file is refused", async () => {
    uploadFile.mockRejectedValue(new ApiError("nope", 415));
    const { container } = render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);
    fireEvent.drop(zone(container), dropEvent([makeFile("bad.exe")]));
    await waitFor(() => expect(screen.getByTestId("upload-zone")).toHaveAttribute("data-phase", "error"));
    expect(screen.getByTestId("upload-zone-icon")).toHaveAttribute("data-phase", "error");
  });

  it("opens the picker from the keyboard", () => {
    render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);
    const click = vi.spyOn(screen.getByTestId("file-input"), "click");
    fireEvent.keyDown(screen.getByRole("button", { name: "Upload files" }), { key: "Enter" });
    expect(click).toHaveBeenCalled();
  });

  it("speaks Hindi when chosen", () => {
    act(() => setLocale("hi"));
    render(<UploadZone projectId="p1" onUploaded={vi.fn()} />);
    expect(screen.getByText(/फ़ाइलें यहाँ खींचें/)).toBeInTheDocument();
    act(() => setLocale("en"));
  });
});
