/**
 * A PDF's pages as text, one string of lines per page, through pdf.js.
 */

/**
 * Undo the glyph substitutions a font without a Unicode map inflicts, where
 * the reading is unambiguous: after a number, " m m" is a mangled µm (a real
 * "mm" never carries the space) and " 1 C" is a mangled °C. A lost µ that
 * mined as mm is a thousandfold error, so these two are corrected at the
 * source; ≥ and ± read wrong too but have no safe rewrite, and stay as they
 * are — a flag, not a guess.
 */
export function unmangle(text: string): string {
  return text.replace(/(\d) ?m m\b/g, '$1 µm').replace(/(\d) 1 C\b/g, '$1 °C');
}

export async function pdfPages(bytes: Uint8Array): Promise<string[]> {
  const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
  const task = pdfjs.getDocument({ data: bytes, useSystemFonts: true, disableFontFace: true, verbosity: 0 });
  const doc = await task.promise;
  const pages: string[] = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    let text = '';
    for (const item of content.items) {
      if (!('str' in item)) continue;
      text += item.str;
      text += item.hasEOL ? '\n' : ' ';
    }
    pages.push(unmangle(text));
    page.cleanup();
  }
  await task.destroy();
  return pages;
}

/** The first DOI in a text, trailing punctuation dropped. */
export function findDoi(text: string): string | undefined {
  const m = text.match(/\b10\.\d{4,9}\/[^\s"<>)\]]+/);
  if (!m) return undefined;
  return m[0].replace(/[.,;:]+$/, '');
}
