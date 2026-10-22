/**
 * The pdf.js worker module ships without type declarations. lib/pdf.ts only
 * imports it for its side effect (it registers globalThis.pdfjsWorker so
 * pdf.js can run without a separate worker script) and never reads an export.
 */
declare module "pdfjs-dist/build/pdf.worker.min.mjs";
