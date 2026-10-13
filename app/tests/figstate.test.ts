import { describe, expect, it } from 'vitest';
import {
  dropLabel, figureLabel, figureName, figureOf, figuresDone, figuresOn, outcomeTag, overlayText, paneNote, placeholder, readsFigures, stateChip, treeTag,
  type FigureOutcome, type FigureOutcomeKind, type FigureState, type FigureTag, type Figured,
} from '../src/renderer/figstate';

/** a paper's figure_state, every count 0 but those given */
const st = (over: Partial<FigureState>): FigureState => ({
  kind: 'read', figures: 0, numbered: 0, pinned: 0, no_plot: 0, no_caption: 0, unnumbered: 0, same_number: 0, small: 0, unplaced: 0, unread: 0,
  plots: 0, read: 0, values: 0, unmatched: null, pages: null, pages_read: null, file: null, at: null, note: null, asked: null, ...over,
});
const xml = (over: Partial<FigureState>, paper: Partial<Figured> = {}): Figured => ({ format: 'jats', pmcid: null, figures_file: over.file ?? null, figure_state: st(over), ...paper });
const pdf = (over: Partial<FigureState>): Figured => ({ format: 'pdf', figure_state: st({ file: 'doi_1.pdf', ...over }) });
const fig = (outcome: FigureOutcomeKind, over: Partial<FigureOutcome> = {}): FigureOutcome => ({ outcome, label: '2', plots: 0, read: 0, page: null, bbox: null, detail: null, ...over });

const ADVICE = 'A PDF of the paper draws its figures: drop one on this figure, or use Collect PDFs on the Search tab.';
const OFF = 'Figures are off (LITRAG_FIGURES=off): a PDF dropped here is kept, not read.';
/** every outcome a figure can come to: a kind the worker adds and this list lacks fails the typecheck */
const KINDS = Object.keys({
  plots: 1, 'no-plot': 1, 'no-caption': 1, skipped: 1, 'no-pdf': 1, unnumbered: 1, 'same-number': 1, small: 1, unplaced: 1, unrecorded: 1, unread: 1,
} satisfies Record<FigureOutcomeKind, 1>) as FigureOutcomeKind[];

describe('the chip on a paper’s card', () => {
  it('says an XML paper’s figures have no PDF, and what PMC said', () => {
    const chip = stateChip(xml({ kind: 'no-pdf', figures: 7, numbered: 6, unnumbered: 1 }));
    expect(chip?.text).toBe('6 figures · no PDF'); // the figures a PDF can be matched to, as Collect counts them
    expect(chip?.title).toBe('Read from XML, which names its figures but holds none. A PDF of the paper draws them: drop one on a figure, or use Collect PDFs on the Search tab.');
    expect(stateChip(xml({ kind: 'no-pdf', figures: 6, numbered: 6, note: 'PMC Cloud: not in its open-access datasets; bulk area: HTTP 404' }))?.title).toBe(
      'Read from XML, which names its figures but holds none. A PDF of the paper draws them: drop one on a figure, or use Collect PDFs on the Search tab. PMC: PMC Cloud: not in its open-access datasets; bulk area: HTTP 404.',
    );
  });

  it('says when no caption gives a number to match a PDF by', () => {
    expect(stateChip(xml({ kind: 'no-pdf', figures: 5, unnumbered: 5 }))).toEqual({ text: '5 figures · none numbered', title: 'Their captions give no number (“Figure 2”), so no PDF can be matched to them.' });
  });

  it('says the PDF kept for the figures is gone from papers/', () => {
    expect(stateChip(xml({ kind: 'missing-file', figures: 3, numbered: 3, file: 'doi_1.figures.pdf' }))).toEqual({ text: 'figures PDF missing', title: 'doi_1.figures.pdf, kept for its figures, is not in papers/: drop it again, or use Collect PDFs.' });
    expect(stateChip(pdf({ kind: 'missing-file', figures: 3 }))?.title).toBe('doi_1.pdf, the PDF the paper was read from, is not in papers/.'); // a PDF paper's own file: nothing kept, nothing to collect
  });

  it('says the figures are not read yet, and how to read them', () => {
    expect(stateChip(xml({ kind: 'unread', figures: 3, numbered: 3, file: 'doi_1.figures.pdf' }))).toEqual({
      text: 'figures not read',
      title: 'A PDF of the paper is kept beside the XML (doi_1.figures.pdf); its figures are not read yet: click a figure and Read the figures.',
    });
    expect(stateChip(pdf({ kind: 'unread', figures: 3 }))).toEqual({ text: 'figures not read', title: 'Its figures are not read yet: click a figure and Read the figures.' });
  });

  it('counts a read paper’s figures with plots, or says it has none', () => {
    const pone = { kind: 'read' as const, pinned: 10, figures: 16, numbered: 16, no_plot: 6, no_caption: 0, pages: 34, pages_read: 16, file: 'pone.figures.pdf' };
    expect(stateChip(xml(pone))?.text).toBe('plots in 10 of 16 figures'); // doi:10.1371/journal.pone.0359084 (M8's acceptance)
    expect(stateChip(pdf({ kind: 'read', figures: 16, no_plot: 16 }))?.text).toBe('no plots in 16 figures');
  });

  it('counts one figure, and one plot, in the singular', () => {
    expect(stateChip(xml({ kind: 'no-pdf', figures: 1, numbered: 1 }))?.text).toBe('1 figure · no PDF');
    expect(stateChip(xml({ kind: 'no-pdf', figures: 1, unnumbered: 1 }))?.text).toBe('1 figure · none numbered');
    expect(stateChip(pdf({ kind: 'read', figures: 1, no_plot: 1 }))?.text).toBe('no plots in 1 figure');
    expect(stateChip(pdf({ kind: 'read', figures: 1, no_plot: 1, small: 1, unplaced: 1, unread: 1 }))?.title.split('\n')).toEqual([
      '1 with no chart the reader found in it.', '1 smaller than an inch each way: not read for charts.', '1 with no place on a page.', '1 not read.',
    ]);
    expect(stateChip(xml({ kind: 'read', figures: 1, pinned: 1, plots: 1, read: 1, values: 1, unmatched: 1, file: 'f.pdf' }))).toEqual({
      text: 'plots in 1 of 1 figure',
      title: 'From f.pdf: 1 of 1 plot read, 1 value.\n1 plot under no caption of the paper’s.',
    });
  });

  it('shows nothing for a paper with no figure, nor with the figures off; a read paper keeps its chip with them off', () => {
    expect(stateChip(xml({ kind: 'none' }))).toBeNull();
    expect(stateChip(xml({ kind: 'off', figures: 6, numbered: 6 }))).toBeNull();
    expect(stateChip({ format: 'jats', figure_state: null })).toBeNull(); // a worker with no row of it
    expect(stateChip(pdf({ kind: 'read', figures: 2, pinned: 2, plots: 2, read: 2 }))?.text).toBe('plots in 2 of 2 figures'); // states() says 'read' whatever the switch
  });

  it('gives a read paper’s title one line for each count that is not zero', () => {
    const lines = (f: Figured) => stateChip(f)!.title.split('\n');
    expect(lines(xml({ figures: 29, pinned: 23, plots: 29, read: 23, values: 218, file: 'doi_1.figures.pdf' }))).toEqual(['From doi_1.figures.pdf: 23 of 29 plots read, 218 values.']);
    expect(lines(xml({ figures: 6, no_plot: 6 }))).toEqual(['6 with no chart the reader found under their caption.']);
    expect(lines(pdf({ figures: 6, no_plot: 6 }))).toEqual(['6 with no chart the reader found in them.']);
    expect(lines(xml({ figures: 4, no_caption: 4 }))).toEqual(['4 whose caption the PDF does not print.']);
    expect(lines(xml({ figures: 1, no_plot: 1, unmatched: 3 }))).toEqual(['1 with no chart the reader found under its caption.', '3 plots under no caption of the paper’s.']);
    expect(lines(xml({ figures: 1, no_plot: 1, pages: 34, pages_read: 16 }))).toEqual(['1 with no chart the reader found under its caption.', 'Pages with a caption of the paper’s: 16 of 34; the rest were not looked at.']);
    expect(lines(pdf({ figures: 1, no_plot: 1 }))).toEqual(['1 with no chart the reader found in it.']);
    expect(lines(xml({ figures: 2, unnumbered: 2 }))).toEqual(['2 with no number to match by.']);
    expect(lines(xml({ figures: 1, same_number: 1 }))).toEqual(['1 numbered as an earlier figure is.']);
    // beyond §4: the counts M8 gives that it has no line for
    expect(lines(pdf({ figures: 2, no_plot: 2, small: 106 }))).toEqual(['2 with no chart the reader found in them.', '106 smaller than an inch each way: not read for charts.']);
    expect(lines(pdf({ figures: 2, no_plot: 2, unplaced: 2 }))).toEqual(['2 with no chart the reader found in them.', '2 with no place on a page.']);
    expect(lines(pdf({ figures: 1, unread: 1 }))).toEqual(['1 not read.']); // skipped, or read before outcomes were kept
    expect(lines(xml({ figures: 3, pinned: 3, plots: 3, read: 3, values: 9, pages: 5, pages_read: 5 }))[1]).toBeUndefined(); // every page looked at: nothing to say of the rest
  });

  it('says nothing of no plot under no caption, the count every read gives', () => {
    const lines = (f: Figured) => stateChip(f)!.title.split('\n');
    expect(lines(xml({ figures: 3, pinned: 3, plots: 3, read: 3, values: 9, unmatched: 0, file: 'f.pdf' }))).toEqual(['From f.pdf: 3 of 3 plots read, 9 values.']);
  });

  it('says what a read came to from its plots, all read or none', () => {
    const lines = (f: Figured) => stateChip(f)!.title.split('\n');
    expect(lines(pdf({ figures: 1, pinned: 1, plots: 2, read: 0 }))).toEqual(['From doi_1.pdf: 0 of 2 plots read, 0 values.']); // plots found, none read
  });

  it('gives the title’s lines in one order', () => {
    const every = { figures: 20, pinned: 2, plots: 3, read: 2, values: 5, no_plot: 2, no_caption: 2, unmatched: 2, pages: 9, pages_read: 4, unnumbered: 2, same_number: 2, small: 2, unplaced: 2, unread: 2, file: 'f.pdf' };
    expect(stateChip(xml(every))!.title.split('\n')).toEqual([
      'From f.pdf: 2 of 3 plots read, 5 values.',
      '2 with no chart the reader found under their caption.',
      '2 whose caption the PDF does not print.',
      '2 plots under no caption of the paper’s.',
      'Pages with a caption of the paper’s: 4 of 9; the rest were not looked at.',
      '2 with no number to match by.',
      '2 numbered as an earlier figure is.',
      // beyond §4
      '2 smaller than an inch each way: not read for charts.',
      '2 with no place on a page.',
      '2 not read.',
    ]);
  });
});

describe('the tag on a figure in the tree', () => {
  it('says what each figure came to', () => {
    expect(outcomeTag(fig('plots', { plots: 3, read: 3 }))).toBe('3 plots');
    expect(outcomeTag(fig('plots', { plots: 3, read: 2 }))).toBe('2 of 3 plots read');
    expect(outcomeTag(fig('plots', { plots: 1, read: 1 }))).toBe('1 plot');
    expect(outcomeTag(fig('plots', { plots: 1, read: 0 }))).toBe('0 of 1 plot read');
    expect(outcomeTag(fig('no-plot'))).toBe('no chart');
    expect(outcomeTag(fig('no-caption'))).toBe('not in PDF');
    expect(outcomeTag(fig('unnumbered', { label: null }))).toBe('no number');
    expect(outcomeTag(fig('same-number'))).toBe('number taken');
    expect(outcomeTag(fig('small'))).toBe('small');
    expect(outcomeTag(fig('unplaced'))).toBe('no place');
    expect(outcomeTag(fig('unread'))).toBe('not read');
    expect(outcomeTag(fig('skipped'))).toBe('not read');
    expect(outcomeTag(fig('unrecorded'))).toBe('not read');
    expect(outcomeTag(fig('no-pdf'))).toBeNull(); // the card says it once
  });

  it('names a figure by the number its caption gives it', () => {
    expect([figureName('2'), figureName('S2'), figureName('Scheme 1'), figureName(null)]).toEqual(['figure 2', 'figure S2', 'scheme 1', 'figure']);
  });

  it('finds each figure’s word by its node, a picture’s and a chart’s, and none for another node', () => {
    const figures: Record<string, FigureTag> = {
      'k#p1': fig('plots', { plots: 2, read: 2 }),
      'k#c1': fig('no-plot', { label: 'Scheme 1' }),
      'k#p2': fig('no-pdf'),
      'k#p3': fig('unnumbered', { label: null }),
      'k#t1': fig('plots'), // a word on a node that is no figure is not read
    };
    const picture = (id: string) => ({ type: 'picture', node_id: id });
    expect(figureOf(figures, picture('k#p1'))).toBe(figures['k#p1']);
    expect(figureOf(figures, { type: 'chart', node_id: 'k#c1' })).toBe(figures['k#c1']);
    expect(figureOf(figures, { type: 'table', node_id: 'k#t1' })).toBeUndefined();
    expect(figureOf(undefined, picture('k#p1'))).toBeUndefined(); // a tree with no word on its figures
    expect([figureLabel(figures, picture('k#p1')), figureLabel(figures, { type: 'chart', node_id: 'k#c1' }), figureLabel(figures, picture('k#p3')), figureLabel(undefined, picture('k#p1'))]).toEqual(['figure 2', 'scheme 1', 'figure', 'figure']);
    expect(figureLabel(figures, { type: 'chart', node_id: 'k#none' })).toBeNull(); // a chart with no number: its text names it
    expect(figureLabel(figures, { type: 'paragraph', node_id: 'k#p1' })).toBeNull();
    expect(treeTag(figures, picture('k#p1'))).toEqual({ text: '2 plots', cls: 'figout o-plots' });
    expect(treeTag(figures, { type: 'chart', node_id: 'k#c1' })).toEqual({ text: 'no chart', cls: 'figout o-no-plot' });
    expect(treeTag(figures, picture('k#p2'))).toBeNull(); // no-pdf: the card says it once
    expect(treeTag(figures, { type: 'table', node_id: 'k#t1' })).toBeNull();
    expect(treeTag(undefined, picture('k#p1'))).toBeNull();
  });
});

describe('the placeholder in an XML paper’s reading', () => {
  const kept = xml({ kind: 'read', figures: 3, file: 'doi_1.figures.pdf' });
  it('says where the figure’s image is, or why there is none', () => {
    expect(placeholder(fig('no-pdf'), xml({ kind: 'no-pdf' }))).toBe('figure 2 — the XML names it and holds no image; no PDF of the paper is kept');
    expect(placeholder(fig('plots', { page: 14, plots: 3, read: 2 }), kept)).toBe('figure 2 — its caption on p. 14 of the PDF kept beside the XML: 2 of 3 plots read');
    expect(placeholder(fig('plots', { page: 14, plots: 3, read: 3 }), kept)).toBe('figure 2 — its caption on p. 14 of the PDF kept beside the XML: 3 of 3 plots read'); // {read} of {plots}, all read too
    expect(placeholder(fig('plots', { page: 14, plots: 1, read: 1 }), kept)).toBe('figure 2 — its caption on p. 14 of the PDF kept beside the XML: 1 of 1 plot read');
    expect(placeholder(fig('no-plot', { page: 5 }), kept)).toBe('figure 2 — its caption on p. 5 of the PDF kept beside the XML; no plot found there was pinned to it');
    expect(placeholder(fig('no-caption'), kept)).toBe('figure 2 — the PDF kept beside the XML prints no caption numbered 2');
    expect(placeholder(fig('unnumbered', { label: null }), kept)).toBe('figure — its caption gives no number, so no PDF can be matched to it');
    expect(placeholder(fig('same-number', { label: '1' }), kept)).toBe('figure — numbered 1, as an earlier figure is; a PDF’s plots go to that one');
    expect(placeholder(fig('unread'), xml({ kind: 'unread', file: 'doi_1.figures.pdf' }))).toBe('figure 2 — a PDF is kept beside the XML, and its figures are not read yet');
  });

  it('says “in” the PDF when the page is not known', () => {
    expect(placeholder(fig('plots', { plots: 3, read: 3 }), kept)).toBe('figure 2 — its caption in the PDF kept beside the XML: 3 of 3 plots read');
    expect(placeholder(fig('no-plot'), kept)).toBe('figure 2 — its caption in the PDF kept beside the XML; no plot found there was pinned to it');
  });

  it('never says a PDF is kept that is gone, and says the rest by their tag', () => {
    const gone = xml({ kind: 'missing-file', figures: 3, file: 'doi_1.figures.pdf' });
    expect(placeholder(fig('unread'), gone)).toBe('figure 2 — doi_1.figures.pdf, kept for its figures, is not in papers/');
    // read before it went: what was read stays read, and the file is named as gone
    expect(placeholder(fig('plots', { page: 14, plots: 2, read: 2 }), gone)).toBe('figure 2 — its caption on p. 14 of doi_1.figures.pdf, kept for its figures and not in papers/ now: 2 of 2 plots read');
    expect(placeholder(fig('no-plot', { page: 5 }), gone)).toBe('figure 2 — its caption on p. 5 of doi_1.figures.pdf, kept for its figures and not in papers/ now; no plot found there was pinned to it');
    expect(placeholder(fig('no-caption'), gone)).toBe('figure 2 — doi_1.figures.pdf, kept for its figures and not in papers/ now, prints no caption numbered 2');
    for (const [o, said] of [['unrecorded', 'not read'], ['skipped', 'not read'], ['small', 'small'], ['unplaced', 'no place']] as const) expect(placeholder(fig(o), kept)).toBe(`figure 2 — ${said}`);
    expect(placeholder(undefined, kept)).toBe('figure — the image itself is not in the XML'); // a tree with no word on its figures
    expect(placeholder(fig('same-number', { label: 'Scheme 1' }), kept)).toBe('figure — numbered Scheme 1, as an earlier figure is; a PDF’s plots go to that one');
  });
});

describe('the line under a figure’s pane, and its one button', () => {
  const asked = 'PMC Cloud: timed out';
  const note = 'PMC Cloud: not in its open-access datasets; bulk area: HTTP 404';
  const kept = { kind: 'read' as const, figures: 4, file: 'doi_1.figures.pdf' };
  /** every row of the pane's table: the outcome, the paper, the line and the button with the figures on */
  const rows: [string, FigureOutcome, Figured, string | null, 'read' | 'ask' | 'ask-again' | null][] = [
    ['no-pdf, PMC’s note', fig('no-pdf'), xml({ kind: 'no-pdf', note, asked: note }, { pmcid: 'PMC1' }), `PMC has no PDF of it (${note}). ${ADVICE}`, 'ask-again'],
    ['no-pdf, the last ask unsettled', fig('no-pdf'), xml({ kind: 'no-pdf', asked }, { pmcid: 'PMC1' }), `PMC could not be asked (${asked}). ${ADVICE}`, 'ask-again'],
    ['no-pdf, never asked', fig('no-pdf'), xml({ kind: 'no-pdf' }, { pmcid: 'PMC1' }), `No answer from PMC on record. ${ADVICE}`, 'ask'],
    ['no-pdf, no PMCID', fig('no-pdf'), xml({ kind: 'no-pdf' }),
      'Read from XML, which names its figures but holds none. A PDF of the paper draws them: drop one here, or fetch it with Collect PDFs on the Search tab, and its charts are read and pinned to these figures.', null],
    // beyond §4's table: PMC's note on a paper with no PMCID (none the worker gives: a note is PMC's answer to a PMCID), and nothing to ask it again by
    ['no-pdf, PMC’s note, no PMCID', fig('no-pdf'), xml({ kind: 'no-pdf', note }), `PMC has no PDF of it (${note}). ${ADVICE}`, null],
    ['no-pdf, an unsettled ask, no PMCID', fig('no-pdf'), xml({ kind: 'no-pdf', asked }),
      'Read from XML, which names its figures but holds none. A PDF of the paper draws them: drop one here, or fetch it with Collect PDFs on the Search tab, and its charts are read and pinned to these figures.', null],
    ['unnumbered', fig('unnumbered', { label: null }), xml({ kind: 'no-pdf' }, { pmcid: 'PMC1' }), 'Its caption gives it no number (“Figure 2”), so no PDF’s charts can be pinned to it.', null],
    ['no-plot, XML', fig('no-plot', { page: 6 }), xml(kept),
      'Its caption is on p. 6 of doi_1.figures.pdf, the PDF kept beside the XML; no plot the reader found on that page was pinned to it. A photograph, a diagram or a curve stays without numbers.', null],
    ['no-plot, XML, its page not known', fig('no-plot'), xml(kept),
      'Its caption is in doi_1.figures.pdf, the PDF kept beside the XML; no plot the reader found on that page was pinned to it. A photograph, a diagram or a curve stays without numbers.', null],
    ['no-plot, XML, its PDF gone since', fig('no-plot', { page: 6 }), xml({ ...kept, kind: 'missing-file' }),
      'Its caption is on p. 6 of doi_1.figures.pdf, kept for its figures and not in papers/ now; no plot the reader found on that page was pinned to it. A photograph, a diagram or a curve stays without numbers.', null],
    ['no-plot, PDF', fig('no-plot', { page: 6 }), pdf({ kind: 'read', figures: 4 }), 'Read: nothing in it is a chart the reader knows (bars, points). A photograph, a diagram or a curve stays without numbers.', null],
    ['no-caption', fig('no-caption', { label: '4' }), xml({ ...kept, unmatched: 0 }), 'doi_1.figures.pdf, the PDF kept beside the XML, prints no caption numbered 4, so nothing in it was pinned here.', null],
    ['no-caption, plots under none', fig('no-caption', { label: '4' }), xml({ ...kept, unmatched: 3 }),
      'doi_1.figures.pdf, the PDF kept beside the XML, prints no caption numbered 4, so nothing in it was pinned here. 3 plots in it sat under no caption of the paper’s.', null],
    ['no-caption, one plot under none', fig('no-caption', { label: '4' }), xml({ ...kept, unmatched: 1 }),
      'doi_1.figures.pdf, the PDF kept beside the XML, prints no caption numbered 4, so nothing in it was pinned here. 1 plot in it sat under no caption of the paper’s.', null],
    ['no-caption, its PDF gone since', fig('no-caption', { label: '4' }), xml({ ...kept, kind: 'missing-file', unmatched: 0 }),
      'doi_1.figures.pdf, kept for its figures and not in papers/ now, prints no caption numbered 4, so nothing in it was pinned here.', null],
    ['small', fig('small'), pdf({ kind: 'read', figures: 4, small: 1 }), 'Smaller than an inch each way: not read for charts.', null],
    ['unplaced', fig('unplaced'), pdf({ kind: 'read', figures: 4, unplaced: 1 }), 'No place on a page: not read for charts.', null],
    ['same-number', fig('same-number', { label: '1' }), xml(kept), 'Its caption’s number, 1, is an earlier figure’s too: a PDF’s plots for figure 1 are pinned to that one.', null],
    ['same-number, a supplement’s', fig('same-number', { label: 'S1' }), xml(kept), 'Its caption’s number, S1, is an earlier figure’s too: a PDF’s plots for figure S1 are pinned to that one.', null],
    ['same-number, a scheme’s', fig('same-number', { label: 'Scheme 1' }), xml(kept), 'Its caption’s number, Scheme 1, is an earlier figure’s too: a PDF’s plots for scheme 1 are pinned to that one.', null],
    ['skipped', fig('skipped', { detail: 'page 9 is past the end of doi_1.pdf' }), pdf({ kind: 'read', figures: 4 }), 'page 9 is past the end of doi_1.pdf', null],
    ['unread, XML', fig('unread'), xml({ kind: 'unread', figures: 4, file: 'doi_1.figures.pdf' }), null, 'read'],
    ['unread, PDF', fig('unread'), pdf({ kind: 'unread', figures: 4 }), null, 'read'],
    ['plots, XML', fig('plots', { page: 14, plots: 3, read: 3 }), xml(kept), 'From p. 14 of doi_1.figures.pdf, the PDF kept beside the XML, pinned by its caption’s number.', null],
    ['plots, XML, its page not known', fig('plots', { plots: 3, read: 3 }), xml(kept), 'From doi_1.figures.pdf, the PDF kept beside the XML, pinned by its caption’s number.', null],
    ['plots, XML, its PDF gone since', fig('plots', { page: 14, plots: 3, read: 3 }), xml({ ...kept, kind: 'missing-file' }), 'From p. 14 of doi_1.figures.pdf, kept for its figures and not in papers/ now, pinned by its caption’s number.', null],
    ['plots, PDF', fig('plots', { page: 14, plots: 3, read: 3 }), pdf({ kind: 'read', figures: 4 }), null, null], // the head says it
    ['unrecorded', fig('unrecorded'), xml(kept), null, null], // read before outcomes were kept: never Read again (K14)
    ['unread, its PDF gone', fig('unread'), xml({ kind: 'missing-file', figures: 4, file: 'doi_1.figures.pdf' }), 'doi_1.figures.pdf, kept for its figures, is not in papers/: drop it again, or use Collect PDFs.', null], // nothing to read
    ['unread, a PDF paper’s own file gone', fig('unread'), pdf({ kind: 'missing-file', figures: 4 }), 'doi_1.pdf, the PDF the paper was read from, is not in papers/.', null], // nothing to read either
  ];

  for (const [name, outcome, paper, line, button] of rows) {
    it(`says ${name}`, () => {
      expect(paneNote(outcome, paper, true)).toEqual({ line, button });
    });
  }

  /** every kind of paper a figure can be of: XML and PDF, read, not read, its file gone, and an XML paper with no PDF PMC was or was not asked for */
  const papers: [string, Figured][] = [
    ['XML, read', xml(kept)],
    ['XML, not read', xml({ kind: 'unread', figures: 4, file: 'doi_1.figures.pdf' })],
    ['XML, its PDF gone', xml({ kind: 'missing-file', figures: 4, file: 'doi_1.figures.pdf' })],
    ['XML, no PDF, never asked', xml({ kind: 'no-pdf', figures: 4 }, { pmcid: 'PMC1' })],
    ['XML, no PDF, asked', xml({ kind: 'no-pdf', figures: 4, asked }, { pmcid: 'PMC1' })],
    ['XML, no PDF, PMC’s note', xml({ kind: 'no-pdf', figures: 4, note }, { pmcid: 'PMC1' })],
    ['XML, no PDF, no PMCID', xml({ kind: 'no-pdf', figures: 4 })],
    ['PDF, read', pdf({ kind: 'read', figures: 4 })],
    ['PDF, not read', pdf({ kind: 'unread', figures: 4 })],
    ['PDF, its file gone', pdf({ kind: 'missing-file', figures: 4 })],
  ];

  it('offers Read the figures only for a figure not read whose file is there, and an ask only of a figure no PDF draws', () => {
    for (const o of KINDS) {
      for (const [name, paper] of papers) {
        const { button } = paneNote(fig(o), paper, true);
        expect(button === 'read', `${o} on ${name}`).toBe(o === 'unread' && paper.figure_state!.kind !== 'missing-file');
        if (button === 'ask' || button === 'ask-again') expect(o, name).toBe('no-pdf');
        expect(paneNote(fig(o), paper, false).button, `${o} on ${name}, the figures off`).toBeNull();
      }
    }
  });

  it('offers nothing with the figures off, and says so on a figure not read', () => {
    for (const [name, outcome, paper] of rows) expect(paneNote(outcome, { ...paper, figure_state: { ...paper.figure_state!, kind: paper.figure_state!.kind === 'read' ? 'read' : 'off' } }, false).button, name).toBeNull();
    expect(paneNote(fig('no-pdf'), xml({ kind: 'off', note }, { pmcid: 'PMC1' }), false)).toEqual({ line: OFF, button: null });
    expect(paneNote(fig('unread'), xml({ kind: 'off', file: 'doi_1.figures.pdf' }), false)).toEqual({ line: OFF, button: null });
    // a PDF paper's figure takes no drop of its own (figuredrop.ts): a PDF dropped there is a paper of its own
    expect(paneNote(fig('unread'), pdf({ kind: 'off', figures: 4 }), false)).toEqual({ line: 'Figures are off (LITRAG_FIGURES=off): they are not read.', button: null });
    expect(paneNote(fig('unnumbered', { label: null }), xml({ kind: 'off' }), false).line).toBe('Its caption gives it no number (“Figure 2”), so no PDF’s charts can be pinned to it.'); // still true, and not a figure a PDF would read
  });

  it('tells the figures are off as the worker says, or else from any paper the papers op says is off', () => {
    expect(readsFigures([xml({ kind: 'read' }), xml({ kind: 'off' })])).toBe(false);
    expect(readsFigures([xml({ kind: 'read' }), xml({ kind: 'no-pdf' }), { format: 'pdf', figure_state: null }])).toBe(true);
    const noneOff = [xml({ kind: 'read' }), xml({ kind: 'none' })]; // with the figures off, a library of papers read and papers whose captions number only schemes
    expect(figuresOn(false, noneOff)).toBe(false); // the `wanted` op's figures_on
    expect(figuresOn(true, [xml({ kind: 'off' })])).toBe(true);
    expect(figuresOn(null, noneOff)).toBe(true); // not said yet: the papers op is read
    expect(figuresOn(undefined, [xml({ kind: 'off' })])).toBe(false);
  });

  it('says nothing of a figure it has no word on', () => {
    expect(paneNote(null, xml({ kind: 'no-pdf' }), true)).toEqual({ line: null, button: null });
  });
});

describe('the drop overlay and the log', () => {
  it('says where a PDF dropped goes', () => {
    expect(dropLabel(true)).toBe('Drop to keep the PDF for this paper’s figures');
    expect(dropLabel(false)).toBe('Drop to ingest');
    // what lies under the drag, topmost first: the overlay, then a figure of a paper read from XML, or nothing that names one
    expect(overlayText([{ id: 'drop-overlay', dataset: {} }, { dataset: {} }, { dataset: { figuresFor: 'pmcid:PMC1' } }, { dataset: {} }])).toBe('Drop to keep the PDF for this paper’s figures');
    expect(overlayText([{ id: 'drop-overlay', dataset: {} }, { dataset: {} }])).toBe('Drop to ingest');
    expect(overlayText([])).toBe('Drop to ingest');
  });

  it('ends a figures run with its counts, and the plots under no caption when there are any', () => {
    const done = { event: 'done', op: 'figures', papers: 2, figures: 16, pinned: 10, plots: 29, read: 23, values: 218, unmatched: 0, recorded: 0 };
    expect(figuresDone(done)).toBe('figures: 23 of 29 charts read in 2 papers (plots in 10 of 16 figures), 218 values');
    expect(figuresDone({ ...done, unmatched: 3 })).toBe('figures: 23 of 29 charts read in 2 papers (plots in 10 of 16 figures), 218 values, 3 plots under no caption');
    expect(figuresDone({ ...done, unmatched: 1 })).toBe('figures: 23 of 29 charts read in 2 papers (plots in 10 of 16 figures), 218 values, 1 plots under no caption'); // §4's log line: plural only
  });

  it('says what a run recorded, and nothing of it when it recorded none', () => {
    const record = { event: 'done', op: 'figures', papers: 0, figures: 0, pinned: 0, plots: 0, read: 0, values: 0, unmatched: 0, recorded: 73 }; // figures {record: true}
    expect(figuresDone(record)).toBe('figures: 0 of 0 charts read in 0 papers (plots in 0 of 0 figures), 0 values, what each figure came to recorded for 73 papers');
    expect(figuresDone({ ...record, recorded: 0 })).toBe('figures: 0 of 0 charts read in 0 papers (plots in 0 of 0 figures), 0 values');
    expect(figuresDone({ event: 'done', op: 'figures', read: 'x' })).toBe('figures: 0 of 0 charts read in 0 papers (plots in 0 of 0 figures), 0 values'); // a count it cannot read is none
  });
});
