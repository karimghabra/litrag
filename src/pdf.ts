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
 *
 * The one place the rule lies is letter-spaced display text (a running
 * footer sets "5 mm" as "5 m m"), so a match inside a run of single-letter
 * tokens is left alone — there, the spacing is the style, not a lost glyph.
 */
export function unmangle(text: string): string {
  // pdf.js renders glyph gaps as runs of spaces; joinLines collapses them
  // later anyway, so collapse here first or the patterns never see a match.
  const flat = text.replace(/[ \t]{2,}/g, ' ');
  const spaced = (before: string) => /(?:^|\s)(?:[A-Za-z0-9] ){3,}$/.test(before);
  const fix = (pattern: RegExp, to: (digit: string) => string) => (input: string) =>
    input.replace(pattern, (match, digit: string, offset: number) => (spaced(input.slice(Math.max(0, offset - 12), offset)) ? match : to(digit)));
  return fix(/(\d) ?m m\b/g, (d) => `${d} µm`)(fix(/(\d) 1 C\b/g, (d) => `${d} °C`)(flat));
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
