import { describe, expect, it } from 'vitest';
import { figuresUnread, imageAlt, imageNote, imagesButton, imagesChip, imagesDone, readAllButton, sourceName, wantsImages, type FigureImage, type ImageState, type Imaged } from '../src/renderer/figimages';
import type { FigureState, FigureTag } from '../src/renderer/figstate';

const img = (over: Partial<FigureImage> = {}): FigureImage => ({
  path: '/lib/p/figures/doi_1/a-g001.jpg', file: 'figures/doi_1/a-g001.jpg', label: 'Figure 2', caption: 'Turbidity against time at pH 7.4.', source: 'pmc-cloud', width: 800, height: 600, mime: 'image/jpeg', ...over,
});
const ask = (over: Partial<ImageState>): ImageState => ({ figures: 6, had: 6, settled: true, sources: 'pmc-cloud', why: null, at: '2026-11-20T00:00:00Z', ...over });
const st = (over: Partial<FigureState>): FigureState => ({
  kind: 'read', figures: 0, numbered: 0, pinned: 0, no_plot: 0, no_caption: 0, unnumbered: 0, same_number: 0, small: 0, unplaced: 0, unread: 0,
  plots: 0, read: 0, values: 0, unmatched: null, pages: null, pages_read: null, file: null, at: null, note: null, asked: null, ...over,
});
const xml = (over: Partial<Imaged> = {}): Imaged => ({ format: 'jats', status: 'parsed', images: null, ...over });
const tag = (over: Partial<FigureTag>): FigureTag => ({ outcome: 'no-pdf', label: '2', plots: 0, read: 0, page: null, ...over });

describe('an image in an XML paper’s reading', () => {
  it('says where it came from, never that the XML holds no image', () => {
    expect(imageNote([img()], tag({}), xml())).toBe('image from the PMC Cloud Service');
    expect(imageNote([img({ source: 'europepmc' }), img({ source: 'europepmc' })], tag({}), xml())).toBe('2 images, from Europe PMC');
    expect(imageNote([img({ source: 'pdf' })], tag({ outcome: 'unnumbered', label: null }), xml())).toBe('image from a PDF of the paper');
  });
  it('adds what its numbers came to when a PDF kept beside the XML was read', () => {
    const paper = xml({ figures_file: 'doi_1.figures.pdf', figure_state: st({ kind: 'read', file: 'doi_1.figures.pdf' }) });
    expect(imageNote([img()], tag({ outcome: 'plots', plots: 2, read: 1, page: 3 }), paper)).toMatch(/^image from the PMC Cloud Service · figure 2 — its caption.*1 of 2 plots read$/);
  });
  it('has its label and caption for alternative text, cut short when long', () => {
    expect(imageAlt(img())).toBe('Figure 2. Turbidity against time at pH 7.4.');
    expect(imageAlt(img({ label: null, caption: null }))).toBe('figure');
    expect(imageAlt(img({ caption: 'x'.repeat(300) })).length).toBe(158);
  });
  it('names each source', () => {
    expect(sourceName('pmc-cloud')).toBe('the PMC Cloud Service');
    expect(sourceName('somewhere')).toBe('somewhere');
  });
});

describe('which papers want images, and which want their figures read', () => {
  it('wants images for an XML paper never asked, or asked and not settled', () => {
    expect(wantsImages(xml())).toBe(true);
    expect(wantsImages(xml({ images: ask({ had: 2, settled: false }) }))).toBe(true);
    expect(wantsImages(xml({ images: ask({ had: 2, settled: true, why: 'PMC Cloud: an author manuscript' }) }))).toBe(false); // every source answered
    expect(wantsImages(xml({ images: ask({}) }))).toBe(false);
    expect(wantsImages({ format: 'pdf', status: 'parsed' })).toBe(false);
    expect(wantsImages(xml({ status: 'failed' }))).toBe(false);
  });
  it('reads a paper whose figures are unread, or were not read with the figures off while a file draws them', () => {
    expect(figuresUnread({ figure_state: st({ kind: 'unread', file: 'a.pdf' }) })).toBe(true);
    expect(figuresUnread({ figure_state: st({ kind: 'off', file: 'a.pdf' }) })).toBe(true);
    expect(figuresUnread({ figure_state: st({ kind: 'off', file: null }) })).toBe(false); // an XML paper with no PDF: nothing to read
    expect(figuresUnread({ figure_state: st({ kind: 'read', file: 'a.pdf' }) })).toBe(false);
    expect(figuresUnread({})).toBe(false);
  });
});

describe('the Papers tab’s figure buttons', () => {
  const papers: Imaged[] = [xml(), xml({ images: ask({}) }), { format: 'pdf', status: 'parsed', figure_state: st({ kind: 'off', file: 'b.pdf' }) }];
  it('count what they would do, and wait while they run', () => {
    expect(imagesButton(papers, false)).toMatchObject({ label: 'Fetch figure images (1)', disabled: false });
    expect(imagesButton(papers, true)).toMatchObject({ label: 'Fetching figure images…', disabled: true });
    expect(imagesButton([xml({ images: ask({}) })], false)).toMatchObject({ label: 'Fetch figure images', disabled: true });
    expect(readAllButton(papers, false)).toMatchObject({ label: 'Read all figures (1)', disabled: false });
    expect(readAllButton(papers, true).disabled).toBe(true);
    expect(readAllButton([], false)).toMatchObject({ label: 'Read all figures', disabled: true });
  });
});

describe('the chip on an XML paper’s card', () => {
  it('counts the figures with their image', () => {
    expect(imagesChip(xml({ images: ask({}) }))).toMatchObject({ text: '6 images', whole: true });
    const some = imagesChip(xml({ images: ask({ had: 2, settled: false, why: 'PMC Cloud: 4 of 6: no file of that name', sources: 'pmc-cloud, pdf' }) }));
    expect(some?.text).toBe('2 of 6 images');
    expect(some?.title).toBe('The images of 2 of its 6 figures, from the PMC Cloud Service, a PDF of the paper, drawn in its reading. For the rest: PMC Cloud: 4 of 6: no file of that name. Fetch figure images asks again.');
    expect(imagesChip(xml())).toBeNull();
    expect(imagesChip(xml({ images: ask({ figures: 0, had: 0 }) }))).toBeNull();
  });
});

describe('the log line a fetch ends with', () => {
  it('says how many papers have every figure’s image, from where, and who still lacks some', () => {
    expect(imagesDone({ op: 'figure_images', with_figures: 333, every: 250, any: 260, figures: 2472, had: 1900, sources: { 'pmc-cloud': 1800, pdf: 100 }, passed: 10, kept: 3, without: { ACS: 26, Wiley: 14 } })).toBe(
      "figure images: 250 of 333 XML papers with figures have every figure's image, 260 have some (1900 of 2472 figures: 1800 from the PMC Cloud Service, 100 from a PDF of the paper); 10 answered before, passed by; 3 PDFs kept for their figures; still lacking some: ACS 26, Wiley 14",
    );
  });
});
