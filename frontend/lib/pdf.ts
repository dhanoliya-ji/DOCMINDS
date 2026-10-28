// A thin wrapper around pdf.js (Mozilla's PDF renderer, the `pdfjs-dist` package) for...

import { matchingItems } from "@/lib/highlight";

export interface PdfHandle {
  numPages: number;
  renderPage(
    pageNumber: number,
    canvas: HTMLCanvasElement,
    overlay: HTMLCanvasElement | null,
    snippet?: string,
    scale?: number
  ): Promise<number>;
  destroy(): void;
}

let loading: Promise<typeof import("pdfjs-dist")> | null = null;

function loadPdfJs() {
  if (!loading) {
    loading = (async () => {
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
