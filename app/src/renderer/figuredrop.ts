/**
 * A PDF dropped on a figure of a paper read from XML goes to that paper: the worker does not ask
 * it what it is, and keeps it beside the XML for the paper's figures when its first pages name the
 * paper (parser/litrag_parser/figures.py, keep_beside), or says why not. Every figure of such a
 * paper the window draws — its pane of numbers, its place in the reading — carries the paper's key
 * as `data-figures-for`. The drop overlay covers the window while a file is dragged over it, so
 * the figure is found among everything under the drop point (document.elementsFromPoint), not as
 * the drop's target.
 */

/** The key a figure of this paper carries: a paper read from XML's, whose figures only a PDF
 *  beside it draws. None for a paper read from its PDF, which draws its own, nor for no paper:
 *  a PDF dropped there is filed as any dropped file is. */
export function figuresFor(paper: { key: string; format?: string | null } | undefined): string | undefined {
  return paper?.format === 'jats' ? paper.key : undefined;
}

/** The paper whose figure lies under the drop point — the first element there that names one — or null. */
export function figuresTarget(stack: Iterable<object>): string | null {
  for (const e of stack) {
    const key = (e as { dataset?: DOMStringMap }).dataset?.['figuresFor'];
    if (key) return key;
  }
  return null;
}

/** What the ingest request knows of the files dropped on a paper's figure: each PDF is for that
 *  paper's figures. An XML is a paper of its own, and is read as any dropped file is. */
export function knownFor(paths: string[], key: string): Record<string, { paper: string }> {
  return Object.fromEntries(paths.filter((p) => /\.pdf$/i.test(p)).map((p) => [p, { paper: key }]));
}

/** What is known of the files dropped at a point, from everything under it, topmost first
 *  (document.elementsFromPoint): on a paper's figure, each PDF is for that paper; elsewhere,
 *  nothing, and the files are filed by what they say of themselves. */
export function droppedOn(paths: string[], stack: Iterable<object>): Record<string, { paper: string }> {
  const key = figuresTarget(stack);
  return key ? knownFor(paths, key) : {};
}

/** The ingest request for files: `known` only when something is known of them. */
export function ingestParams(lib: string, paths: string[], known: Record<string, { paper: string }>): { lib: string; paths: string[]; known?: Record<string, { paper: string }> } {
  return Object.keys(known).length ? { lib, paths, known } : { lib, paths };
}
