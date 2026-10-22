/**
 * SourceViewer.test.tsx
 * =====================
 * WHAT THIS FILE TESTS
 * --------------------
 * `components/SourceViewer.tsx` -- clicking a citation opens the source.
 *
 *   PDF      the file's bytes are fetched and handed to pdf.js, opened at
 *            the CITED page with the snippet to highlight; prev / next move
 *            through the pages; a PDF that cannot be drawn falls back to text
 *   others   the cited chunk and its neighbours, the passage in a <mark>
 *   dialog   labelled, closes on Escape and on the backdrop
 *
 * pdf.js itself cannot run in jsdom (there is no canvas), so `lib/pdf` is
 * mocked: what is checked is that the viewer asks it for the right page with
 * the right snippet, which is the part this component owns.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SourceViewer, { isPdf } from "@/components/SourceViewer";

const fetchDocumentFile = vi.fn();
const documentChunks = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    fetchDocumentFile: (...a: unknown[]) => fetchDocumentFile(...a),
    documentChunks: (...a: unknown[]) => documentChunks(...a),
  },
  getErrorMessage: (error: unknown, fallback = "Something went wrong.") =>
    error instanceof Error && error.message ? error.message : fallback,
}));

const renderPage = vi.fn();
const destroy = vi.fn();
const openPdf = vi.fn();
vi.mock("@/lib/pdf", () => ({ openPdf: (...a: unknown[]) => openPdf(...a) }));

const PDF_SOURCE = {
  document_id: "d1",
  chunk_id: "c7",
  filename: "handbook.pdf",
  page_number: 3,
  snippet: "Employees accrue 1.75 days per month.",
  number: 2,
};

beforeEach(() => {
  fetchDocumentFile.mockReset().mockResolvedValue(new ArrayBuffer(8));
  renderPage.mockReset().mockResolvedValue(2);
  destroy.mockReset();
  openPdf.mockReset().mockResolvedValue({ numPages: 5, renderPage, destroy });
  documentChunks.mockReset().mockResolvedValue([
    { id: "c6", chunk_index: 6, page_number: 3, content: "The previous chunk." },
    { id: "c7", chunk_index: 7, page_number: 3, content: "Intro. Employees accrue 1.75 days per month. More." },
  ]);
});

describe("which view", () => {
  it("knows a PDF by its extension", () => {
    expect(isPdf("Report.PDF")).toBe(true);
    expect(isPdf("notes.docx")).toBe(false);
  });
});

describe("a PDF source", () => {
  it("opens at the cited page and highlights the passage", async () => {
    render(<SourceViewer source={PDF_SOURCE} onClose={vi.fn()} />);

    await waitFor(() => expect(renderPage).toHaveBeenCalled());
    expect(fetchDocumentFile).toHaveBeenCalledWith("d1");
    const [page, , , snippet] = renderPage.mock.calls[0];
    expect(page).toBe(3);
    expect(snippet).toBe(PDF_SOURCE.snippet);
    expect(await screen.findByText("Page 3 of 5")).toBeInTheDocument();
    expect(await screen.findByText(/cited passage highlighted/i)).toBeInTheDocument();
  });

  it("moves between pages, highlighting only the cited one", async () => {
    const user = userEvent.setup();
    render(<SourceViewer source={PDF_SOURCE} onClose={vi.fn()} />);
    await screen.findByText("Page 3 of 5");

    await user.click(screen.getByRole("button", { name: /next page/i }));

    await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(2));
    const [page, , , snippet] = renderPage.mock.calls[1];
    expect(page).toBe(4);
    expect(snippet).toBeUndefined();
  });

  it("falls back to the extracted text when the file cannot be opened", async () => {
    fetchDocumentFile.mockRejectedValue(new Error("This file was quarantined."));
    const user = userEvent.setup();
    render(<SourceViewer source={PDF_SOURCE} onClose={vi.fn()} />);

    expect(await screen.findByText(/quarantined/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /extracted text/i }));

    expect(await screen.findByText("Employees accrue 1.75 days per month.")).toBeInTheDocument();
  });

  it("can switch to the text view on request", async () => {
    const user = userEvent.setup();
    render(<SourceViewer source={PDF_SOURCE} onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Show text" }));

    const mark = await screen.findByText("Employees accrue 1.75 days per month.");
    expect(mark.tagName).toBe("MARK");
    expect(documentChunks).toHaveBeenCalledWith("d1", "c7", 2);
  });
});

describe("any other source", () => {
  const DOCX = { ...PDF_SOURCE, filename: "policy.docx" };

  it("shows the cited chunk in context, the passage marked", async () => {
    render(<SourceViewer source={DOCX} onClose={vi.fn()} />);

    const mark = await screen.findByText("Employees accrue 1.75 days per month.");
    expect(mark.tagName).toBe("MARK");
    expect(screen.getByText("The previous chunk.")).toBeInTheDocument();
    expect(openPdf).not.toHaveBeenCalled();
  });

  it("says so when the passage no longer exists", async () => {
    documentChunks.mockRejectedValue(new Error("That passage no longer exists."));
    render(<SourceViewer source={DOCX} onClose={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("no longer exists");
  });
});

describe("the dialog", () => {
  it("is labelled with the source", async () => {
    render(<SourceViewer source={{ ...PDF_SOURCE, filename: "policy.docx" }} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Source: policy.docx" })).toBeInTheDocument();
  });

  it("closes on Escape, on the close button and on the backdrop", async () => {
    const onClose = vi.fn();
    const { container } = render(<SourceViewer source={{ ...PDF_SOURCE, filename: "a.txt" }} onClose={onClose} />);

    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: /close source/i }));
    fireEvent.click(container.firstChild as HTMLElement);
    // A click INSIDE the dialog must not close it.
    fireEvent.click(screen.getByRole("dialog"));

    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
