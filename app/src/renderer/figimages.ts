/**
 * An XML paper's figures as images (parser/litrag_parser/figure_images.py), in the window's
 * words: the line under an image in the paper's reading, the Papers tab's two buttons over the
 * whole project — fetch the XML papers' figure images, read every figure not read yet into numbers
 * — and the log line a fetch ends with. Each reads only what the worker answered (the tree's
 * `images`, the papers op's `images` and `figure_state`, a done's counts), so each can be tested
 * apart from the DOM.
 */

import { placeholder, type FigureState, type FigureTag, type Figured } from './figstate.ts';

/** one image of a figure, as the tree op gives it (figure_images.images_for) */
export interface FigureImage {
  /** the file on this machine, read through the main process */
  path: string;
  /** the same, from the library's folder: figures/<key>/<name> */
  file: string;
  /** the label and caption as the XML prints them */
  label: string | null;
  caption: string | null;
  /** where it came from: pmc-cloud | europepmc | pdf */
  source: string;
  width: number | null;
  height: number | null;
  mime: string;
}

/** what an XML paper's figure images came to (figure_images.states): none for a paper never asked */
export interface ImageState {
  figures: number;
  /** figures with an image on disk now */
  had: number;
  /** whether asking the services again would change nothing: every figure had, or each answered for the rest */
  settled: boolean;
  /** whether the PDF fallback answered for good: a PDF of the paper searched, or none to be had */
  fallback: boolean;
  sources: string | null;
  why: string | null;
  at: string | null;
}

/** what these words need to know of a paper: a row of the papers op */
export interface Imaged extends Figured {
  status?: string;
  images?: ImageState | null;
  figure_state?: FigureState | null;
}

const SOURCES: Record<string, string> = {
  'pmc-cloud': 'the PMC Cloud Service',
  europepmc: 'Europe PMC',
  pdf: 'a PDF of the paper',
};

/** "the PMC Cloud Service", "Europe PMC", "a PDF of the paper" */
export function sourceName(source: string): string {
  return SOURCES[source] ?? source;
}

/** The muted line under a figure's images in an XML paper's reading: where they came from, and,
 *  when its numbers were read from a PDF kept beside the XML, what that came to. Never the
 *  placeholder's "holds no image": the image is there. */
export function imageNote(images: FigureImage[], tag: FigureTag | null | undefined, paper: Figured | undefined): string {
  const from = [...new Set(images.map((i) => sourceName(i.source)))].join(' and ');
  const what = images.length > 1 ? `${images.length} images, from ${from}` : `image from ${from}`;
  if (tag && (tag.outcome === 'plots' || tag.outcome === 'no-plot' || tag.outcome === 'no-caption' || tag.outcome === 'unread')) {
    return `${what} · ${placeholder(tag, paper)}`;
  }
  return what;
}

/** An image's alternative text: its label and the start of its caption. */
export function imageAlt(image: FigureImage): string {
  const words = [image.label, image.caption].filter(Boolean).join('. ');
  return words.length > 160 ? `${words.slice(0, 157)}…` : words || 'figure';
}

/** Whether a paper read from XML still lacks the image of some figure that a fetch could change
 *  (figure_images.wanting): never asked, or lacking some while the services or the PDF fallback
 *  have yet to answer for good. */
export function wantsImages(p: Imaged): boolean {
  if (p.format !== 'jats' || p.status !== 'parsed') return false;
  const s = p.images;
  return !s || (s.had < s.figures && !(s.settled && s.fallback));
}

/** Whether a paper's figures are there to read and not read yet: unread, or not read with the
 *  figures off while a file draws them. */
export function figuresUnread(p: Imaged): boolean {
  const s = p.figure_state;
  return !!s && (s.kind === 'unread' || (s.kind === 'off' && !!s.file));
}

export interface ToolButton {
  label: string;
  title: string;
  disabled: boolean;
}

/** The Papers tab's button that fetches the XML papers' figure images (the worker's figure_images). */
export function imagesButton(papers: Imaged[], running: boolean): ToolButton {
  const n = papers.filter(wantsImages).length;
  return {
    label: running ? 'Fetching figure images…' : n ? `Fetch figure images (${n})` : 'Fetch figure images',
    title: n
      ? `Fetch the figure images of ${n} paper${n === 1 ? '' : 's'} read from XML, by PMCID, from the PMC Cloud Service, else Europe PMC, else cut from a PDF of the paper (PMC's, or the open copy OpenAlex names). Papers already answered are passed by.`
      : 'Every paper read from XML has its figure images, or every source has answered for the rest',
    disabled: running || !n,
  };
}

/** The Papers tab's button that reads every figure not read yet into numbers (the worker's figures op). */
export function readAllButton(papers: Imaged[], running: boolean): ToolButton {
  const n = papers.filter(figuresUnread).length;
  return {
    label: running ? 'Reading the figures…' : n ? `Read all figures (${n})` : 'Read all figures',
    title: n
      ? `Read the charts in the figures of ${n} paper${n === 1 ? '' : 's'} not read yet into numbers (bars, points, error bars), on this machine`
      : 'Every paper whose figures a file draws has them read',
    disabled: running || !n,
  };
}

/** The log line a figure_images run ends with. */
export function imagesDone(ev: Record<string, unknown>): string {
  const n = (k: string) => Number(ev[k] ?? 0);
  const sources = (ev['sources'] ?? {}) as Record<string, number>;
  const from = Object.entries(sources).map(([s, c]) => `${c} from ${sourceName(s)}`).join(', ');
  const without = (ev['without'] ?? {}) as Record<string, number>;
  const lacking = Object.entries(without).sort((a, b) => b[1] - a[1]).map(([p, c]) => `${p} ${c}`).join(', ');
  return (
    `figure images: ${n('every')} of ${n('with_figures')} XML papers with figures have every figure's image, ${n('any')} have some` +
    ` (${n('had')} of ${n('figures')} figures${from ? `: ${from}` : ''})` +
    (n('passed') ? `; ${n('passed')} answered before, passed by` : '') +
    (n('kept') ? `; ${n('kept')} PDFs kept for their figures` : '') +
    (lacking ? `; still lacking some: ${lacking}` : '')
  );
}

/** The chip on an XML paper's card saying how many of its figures have their image: none for a
 *  paper never asked, or one with no figure. */
export function imagesChip(p: Imaged): { text: string; title: string; whole: boolean } | null {
  const s = p.images;
  if (p.format !== 'jats' || !s || !s.figures) return null;
  const whole = s.had >= s.figures;
  const from = s.sources ? s.sources.split(', ').map(sourceName).join(', ') : null;
  return {
    text: whole ? `${s.figures} image${s.figures === 1 ? '' : 's'}` : `${s.had} of ${s.figures} images`,
    title: (s.had ? `The images of ${s.had} of its ${s.figures} figures${from ? `, from ${from}` : ''}, drawn in its reading.` : `No image for its ${s.figures} figures yet.`) +
      (!whole && s.why ? ` For the rest: ${s.why}.` : '') + (!whole && !(s.settled && s.fallback) ? ' Fetch figure images asks again.' : ''),
    whole,
  };
}
