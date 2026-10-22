/**
 * highlight.ts
 * ============
 * Finding a cited passage inside the text it came from, so the source viewer
 * can highlight it.
 *
 * WHY THIS IS FUZZIER THAN A PLAIN indexOf
 * ----------------------------------------
 * The citation's snippet is the first 300 characters of a CHUNK, and a chunk
 * is text that went through extraction: line breaks, hyphenation and runs of
 * spaces rarely survive exactly as they appear on the PDF page or in the
 * chunk shown beside it. So both sides are NORMALISED -- lower-cased, every
 * run of whitespace collapsed to one space -- before comparing, and the match
 * is mapped back to positions in the ORIGINAL text so the highlight lands on
 * the real characters.
 */

/** Lower-case and collapse whitespace. */
export function normalizeText(text: string): string {
  return (text || "").toLowerCase().replace(/\s+/g, " ").trim();
}

/**
 * Where `snippet` occurs in `content`, as [start, end) offsets into
 * `content` itself -- or null. Tolerates whitespace and case differences,
 * and a trailing "..." on the snippet.
 */
export function findPassage(content: string, snippet: string): [number, number] | null {
  const needle = normalizeText(snippet.replace(/\.\.\.$/, ""));
  if (!needle) return null;

  // Build the normalised text while remembering, for every character of it,
  // which character of the original it came from.
  const map: number[] = [];
  let normalized = "";
  let lastWasSpace = true; // drops leading whitespace, as trim() would
  for (let i = 0; i < content.length; i++) {
    const ch = content[i];
    if (/\s/.test(ch)) {
      if (!lastWasSpace) {
        normalized += " ";
        map.push(i);
        lastWasSpace = true;
      }
    } else {
      normalized += ch.toLowerCase();
      map.push(i);
      lastWasSpace = false;
    }
  }

  const at = normalized.indexOf(needle);
  if (at === -1) return null;
  return [map[at], map[at + needle.length - 1] + 1];
}

/** A text run on a PDF page, as pdf.js reports it (only what we need). */
export interface PdfTextItem {
  str: string;
}

/**
 * Which text runs on a PDF page belong to the cited passage.
 *
 * pdf.js reports a page's text as many short runs -- roughly one per line or
 * per styled span. A run is marked when its text appears inside the snippet.
 * Very short runs ("of", "12") are skipped: they would appear inside almost
 * any snippet and light up the whole page.
 */
export function matchingItems(items: PdfTextItem[], snippet: string, minLength = 8): number[] {
  const haystack = normalizeText(snippet.replace(/\.\.\.$/, ""));
  if (!haystack) return [];
  const hits: number[] = [];
  items.forEach((item, index) => {
    const text = normalizeText(item.str);
    if (text.length >= minLength && haystack.includes(text)) hits.push(index);
  });
  return hits;
}
