// Finding a cited passage inside the text it came from, so the source viewer can...

export function normalizeText(text: string): string {
  return (text || "").toLowerCase().replace(/\s+/g, " ").trim();
}

export function findPassage(content: string, snippet: string): [number, number] | null {
  const needle = normalizeText(snippet.replace(/\.\.\.$/, ""));
  if (!needle) return null;

  const map: number[] = [];
  let normalized = "";
  let lastWasSpace = true;
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

export interface PdfTextItem {
  str: string;
}

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
