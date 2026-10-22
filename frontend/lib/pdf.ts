/**
 * pdf.ts
 * ======
 * A thin wrapper around pdf.js (Mozilla's PDF renderer, the `pdfjs-dist`
 * package) for the citation viewer: open a PDF from bytes, draw one page onto
 * a canvas, and paint highlight boxes over the cited passage.
 *
 * WHY pdf.js IS LOADED LAZILY
 * ---------------------------
 * It is a large library, and most visits never open a citation. A dynamic
 * `import()` keeps it out of the main bundle: it is downloaded the first time
 * someone clicks "View source", and never otherwise.
 *
 * WHY THE "WORKER" RUNS ON THE MAIN THREAD
 * ----------------------------------------
 * pdf.js normally parses PDFs in a Web Worker loaded from a separate script
 * URL. That URL is awkward here twice over: the bundler has to be taught to
 * emit the worker file, and this app's Content-Security-Policy
 * (security-headers.mjs) allows scripts from 'self' only, which a blob: or
 * CDN worker would break. Importing the worker MODULE instead registers its
 * message handler on `globalThis.pdfjsWorker`, and pdf.js then runs the same
 * code on the main thread with no extra URL at all. For showing one page of a
 * cited document that cost is invisible; for a full PDF reader it would not be.
 *
 * `isEvalSupported: false` for the same CSP reason: pdf.js would otherwise
 * compile font programs with `new Function`, which production CSP forbids.
 */

import { matchingItems } from "@/lib/highlight";

export interface PdfHandle {
  numPages: number;
  /**
   * Draw page `pageNumber` (1-based) at `scale` onto `canvas`, and highlight
   * the runs of text that belong to `snippet` on `overlay` (a second canvas
   * stacked on top). Resolves with how many runs were highlighted.
   */
  renderPage(
    pageNumber: number,
    canvas: HTMLCanvasElement,
    overlay: HTMLCanvasElement | null,
    snippet?: string,
    scale?: number
  ): Promise<number>;
  destroy(): void;
}

// Loaded once per page visit, shared by every viewer opened.
let loading: Promise<typeof import("pdfjs-dist")> | null = null;

function loadPdfJs() {
  if (!loading) {
    loading = (async () => {
      // The worker module first: importing it is what installs the
      // main-thread handler pdf.js looks for (see the header comment).
      await import("pdfjs-dist/build/pdf.worker.min.mjs");
      return import("pdfjs-dist");
    })();
  }
  return loading;
}

export async function openPdf(data: ArrayBuffer): Promise<PdfHandle> {
  const pdfjs = await loadPdfJs();
  const task = pdfjs.getDocument({ data: new Uint8Array(data), isEvalSupported: false });
  const document = await task.promise;

  return {
    numPages: document.numPages,

    async renderPage(pageNumber, canvas, overlay, snippet, scale = 1.4) {
      const page = await document.getPage(Math.min(Math.max(1, pageNumber), document.numPages));
      const viewport = page.getViewport({ scale });

      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      const context = canvas.getContext("2d");
      if (!context) return 0;
      await page.render({ canvas, canvasContext: context, viewport }).promise;

      if (!overlay || !snippet) return 0;
      overlay.width = canvas.width;
      overlay.height = canvas.height;
      const paint = overlay.getContext("2d");
      if (!paint) return 0;
      paint.clearRect(0, 0, overlay.width, overlay.height);

      // The page's text, as positioned runs. Each run's `transform` places
      // it in PDF space; multiplying by the viewport's transform gives
      // canvas pixels.
      const content = await page.getTextContent();
      const items = content.items.filter(
        (item): item is (typeof content.items)[number] & { str: string; transform: number[]; width: number } =>
          "str" in item
      );
      const hits = matchingItems(items, snippet);
      paint.fillStyle = "rgba(250, 204, 21, 0.38)";
      for (const index of hits) {
        const item = items[index];
        const [, , c, d, e, f] = pdfjs.Util.transform(viewport.transform, item.transform);
        // The font size is the length of the transformed vertical vector.
        const height = Math.hypot(c, d);
        paint.fillRect(e, f - height, item.width * viewport.scale, height * 1.15);
      }
      return hits.length;
    },

    destroy() {
      void task.destroy();
    },
  };
}
