/**
 * What a paper's figures came to, in the window's words: the chip on its card, the tag on each
 * figure in its tree, the placeholder an XML paper's reading shows where a figure's image would
 * be, the line under a figure's pane of numbers and the one button it offers, the drop overlay's
 * text over a figure, the log line a figures run ends with, and what asking PMC for the PDFs
 * comes to (the Search tab's button, a Collect entry's title, the log lines). Every word is here,
 * so the wording changes in one place; each function reads only what the worker answered
 * (figures.states, figures.outcomes, the `wanted` op's switch, a done's counts) or, for the
 * overlay, what lies under the drag, so each can be tested apart from the DOM.
 */

import { figuresTarget } from './figuredrop.ts';

/** a paper's figures, counted (figures.states): an XML paper's over its figures (not its schemes or charts), a PDF paper's over the pictures a read looks for (small and unplaced counted apart) */
export interface FigureState {
  /** none: no figure; no-pdf: XML with no PDF kept; missing-file: the file that draws them is not on disk; off: not read, LITRAG_FIGURES=off; unread; read */
  kind: 'none' | 'no-pdf' | 'missing-file' | 'off' | 'unread' | 'read';
  figures: number;
  /** figures whose caption gives a number to match a PDF's captions by */
  numbered: number;
  /** figures with plots pinned to them */
  pinned: number;
  no_plot: number;
  no_caption: number;
  unnumbered: number;
  same_number: number;
  small: number;
  unplaced: number;
  /** figures not read: unread, skipped, or an XML figure with no plot read before outcomes were kept */
  unread: number;
  /** charts rows, a scheme's too */
  plots: number;
  read: number;
  values: number;
  /** plots in the PDF under no caption of the paper's */
  unmatched: number | null;
  /** the PDF's pages, and an XML paper's pages printing a caption of its floats (the only ones looked at) */
  pages: number | null;
  pages_read: number | null;
  /** the file that draws the figures: the paper's own PDF, or the PDF kept beside its XML */
  file: string | null;
  at: string | null;
  /** PMC's settled answer that it has no PDF (papers.figures_note) */
  note: string | null;
  /** the last word on asking PMC for its PDF (a figures-pdf events row) */
  asked: string | null;
}

/** what one figure came to (figures.outcomes): the row its read kept, or what is known without one */
export type FigureOutcomeKind = 'plots' | 'no-plot' | 'no-caption' | 'skipped' | 'no-pdf' | 'unnumbered' | 'same-number' | 'small' | 'unplaced' | 'unrecorded' | 'unread';

/** a picture or chart node's outcome in the tree: its tag and its placeholder */
export interface FigureTag {
  outcome: FigureOutcomeKind;
  /** the key its caption gives it: "2", "S2", "Scheme 1"; null when it gives no number */
  label: string | null;
  plots: number;
  read: number;
  /** a picture's page, or the PDF page an XML figure's plots or caption are on */
  page: number | null;
}

/** one figure's outcome, as the charts op gives it */
export interface FigureOutcome extends FigureTag {
  /** a picture's box, [left, top, right, bottom] in points from the top */
  bbox: number[] | null;
  /** why it was skipped */
  detail: string | null;
}

/** what these words need to know of a paper: a row of the papers op */
export interface Figured {
  format?: string | null;
  pmcid?: string | null;
  figures_file?: string | null;
  figure_state?: FigureState | null;
}

/** "3 plots", "1 plot" */
const count = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** "figure 2", "figure S2", "scheme 1" — or "figure" when its caption gives no number */
export function figureName(label: string | null | undefined): string {
  if (!label) return 'figure';
  const [kind, no] = label.split(' ');
  return no ? `${kind!.toLowerCase()} ${no}` : `figure ${label}`;
}

/** the file that draws the paper's figures, by name */
const fileOf = (paper: Figured | undefined) => paper?.figure_state?.file ?? paper?.figures_file ?? 'the PDF';

/** what an XML paper's PDF is, said after its name: "the PDF kept beside the XML" — or, once that
 *  file is gone from papers/ (missing-file), that it was kept and is not there now: what was read
 *  of it stays read, and is never said to be on disk */
function beside(paper: Figured | undefined): string {
  return paper?.figure_state?.kind === 'missing-file' ? 'kept for its figures and not in papers/ now' : 'the PDF kept beside the XML';
}

/** the same where the file is not named: "the PDF kept beside the XML", or "‹file›, kept for its figures and not in papers/ now" */
function besideNamed(paper: Figured | undefined): string {
  return paper?.figure_state?.kind === 'missing-file' ? `${fileOf(paper)}, ${beside(paper)}` : beside(paper);
}

/** a paper whose figures' file is not in papers/: the PDF kept beside its XML, or its own */
function missingLine(paper: Figured | undefined): string {
  return paper?.format === 'jats' ? `${fileOf(paper)}, kept for its figures, is not in papers/: drop it again, or use Collect PDFs.` : `${fileOf(paper)}, the PDF the paper was read from, is not in papers/.`;
}

// ---- the card --------------------------------------------------------------------------------

/** The chip after a card's XML/PDF badge, and its title: none for a paper with no figure, nor
 *  for one whose figures are off (LITRAG_FIGURES=off) and not read. */
export function stateChip(paper: Figured): { text: string; title: string } | null {
  const s = paper.figure_state;
  if (!s) return null;
  const xml = paper.format === 'jats';
  switch (s.kind) {
    case 'no-pdf':
      if (s.numbered > 0) {
        return {
          text: `${count(s.numbered, 'figure')} · no PDF`,
          title: `Read from XML, which names its figures but holds none. A PDF of the paper draws them: drop one on a figure, or use Collect PDFs on the Search tab.${s.note ? ` PMC: ${s.note}.` : ''}`,
        };
      }
      return { text: `${count(s.figures, 'figure')} · none numbered`, title: 'Their captions give no number (“Figure 2”), so no PDF can be matched to them.' };
    case 'missing-file':
      return { text: 'figures PDF missing', title: missingLine(paper) };
    case 'unread':
      return {
        text: 'figures not read',
        title: xml ? `A PDF of the paper is kept beside the XML (${fileOf(paper)}); its figures are not read yet: click a figure and Read the figures.` : 'Its figures are not read yet: click a figure and Read the figures.',
      };
    case 'read':
      return { text: s.pinned ? `plots in ${s.pinned} of ${count(s.figures, 'figure')}` : `no plots in ${count(s.figures, 'figure')}`, title: readLines(s, xml).join('\n') };
    default: // none, off
      return null;
  }
}

/** A read paper's chip title: one line for each count that is not zero. */
function readLines(s: FigureState, xml: boolean): string[] {
  const lines: string[] = [];
  if (s.plots) lines.push(`From ${s.file ?? 'the PDF'}: ${s.read} of ${count(s.plots, 'plot')} read, ${count(s.values, 'value')}.`);
  if (s.no_plot) lines.push(`${s.no_plot} with no chart the reader found ${xml ? (s.no_plot === 1 ? 'under its caption' : 'under their caption') : s.no_plot === 1 ? 'in it' : 'in them'}.`);
  if (s.no_caption) lines.push(`${s.no_caption} whose caption the PDF does not print.`);
  if (s.unmatched) lines.push(`${count(s.unmatched, 'plot')} under no caption of the paper’s.`);
  if (s.pages !== null && s.pages_read !== null && s.pages_read < s.pages) lines.push(`Pages with a caption of the paper’s: ${s.pages_read} of ${s.pages}; the rest were not looked at.`);
  if (s.unnumbered) lines.push(`${s.unnumbered} with no number to match by.`);
  if (s.same_number) lines.push(`${s.same_number} numbered as an earlier figure is.`);
  if (s.small) lines.push(`${s.small} smaller than an inch each way: not read for charts.`);
  if (s.unplaced) lines.push(`${s.unplaced} with no place on a page.`);
  if (s.unread) lines.push(`${s.unread} not read.`);
  return lines;
}

// ---- the tree --------------------------------------------------------------------------------

/** a node of the tree, as these words need it */
interface TreeNode {
  type: string;
  node_id: string;
}

/** The figure a tree node is: a picture's or a chart's word in the tree's `figures`, or none. */
export function figureOf(figures: Record<string, FigureTag> | null | undefined, n: TreeNode): FigureTag | undefined {
  return n.type === 'picture' || n.type === 'chart' ? figures?.[n.node_id] : undefined;
}

/** A figure's name in the tree: the number its caption gives it ("figure 2", "scheme 1"), "figure"
 *  for a picture with none, and null for a chart with none, or a node that is no figure. */
export function figureLabel(figures: Record<string, FigureTag> | null | undefined, n: TreeNode): string | null {
  const label = figureOf(figures, n)?.label;
  if (label) return figureName(label);
  return n.type === 'picture' ? 'figure' : null;
}

/** The tag a figure's row in the tree carries — its text and its class — or none. */
export function treeTag(figures: Record<string, FigureTag> | null | undefined, n: TreeNode): { text: string; cls: string } | null {
  const fig = figureOf(figures, n);
  const text = fig ? outcomeTag(fig) : null;
  return fig && text ? { text, cls: `figout o-${fig.outcome}` } : null;
}

/** The tag before a figure's page in the tree; none for an XML figure no PDF draws. */
export function outcomeTag(tag: FigureTag): string | null {
  switch (tag.outcome) {
    case 'plots':
      return tag.read === tag.plots ? count(tag.plots, 'plot') : `${tag.read} of ${count(tag.plots, 'plot')} read`;
    case 'no-plot':
      return 'no chart';
    case 'no-caption':
      return 'not in PDF';
    case 'unnumbered':
      return 'no number';
    case 'same-number':
      return 'number taken';
    case 'small':
      return 'small';
    case 'unplaced':
      return 'no place';
    case 'unread':
    case 'skipped':
    case 'unrecorded':
      return 'not read';
    default: // no-pdf
      return null;
  }
}

/** " on p. 14 of", or " in" when the page is not known */
const onPage = (page: number | null) => (page !== null ? ` on p. ${page} of` : ' in');

/** What an XML paper's reading shows where a figure's image would be. */
export function placeholder(tag: FigureTag | null | undefined, paper: Figured | undefined): string {
  if (!tag) return 'figure — the image itself is not in the XML';
  const name = figureName(tag.label);
  switch (tag.outcome) {
    case 'no-pdf':
      return `${name} — the XML names it and holds no image; no PDF of the paper is kept`;
    case 'plots':
      return `${name} — its caption${onPage(tag.page)} ${besideNamed(paper)}: ${tag.read} of ${count(tag.plots, 'plot')} read`;
    case 'no-plot':
      return `${name} — its caption${onPage(tag.page)} ${besideNamed(paper)}; no plot found there was pinned to it`;
    case 'no-caption':
      return `${name} — ${besideNamed(paper)}${paper?.figure_state?.kind === 'missing-file' ? ',' : ''} prints no caption numbered ${tag.label}`;
    case 'unnumbered':
      return 'figure — its caption gives no number, so no PDF can be matched to it';
    case 'same-number':
      return `figure — numbered ${tag.label}, as an earlier figure is; a PDF’s plots go to that one`;
    case 'unread':
      if (paper?.figure_state?.kind === 'missing-file') return `${name} — ${fileOf(paper)}, kept for its figures, is not in papers/`;
      return `${name} — a PDF is kept beside the XML, and its figures are not read yet`;
    default: // skipped, unrecorded, small, unplaced: its tag says it
      return `${name} — ${outcomeTag(tag)}`;
  }
}

// ---- the figure's pane -----------------------------------------------------------------------

/** The muted line under a figure's pane head, and the one button it offers: Read the figures
 *  ('read'), Ask PMC for its PDF ('ask'), Ask PMC again ('ask-again'), or none. */
export interface PaneNote {
  line: string | null;
  button: 'read' | 'ask' | 'ask-again' | null;
}

const ADVICE = 'A PDF of the paper draws its figures: drop one on this figure, or use Collect PDFs on the Search tab.';

/** Whether the worker reads figures (LITRAG_FIGURES), as the papers op shows it, until the worker
 *  has said (figuresOn): with the switch off every paper whose figures are not read is 'off', so one
 *  is enough to tell; a library with none — every paper read, or none with a figure — takes it as on. */
export function readsFigures(papers: Iterable<Figured>): boolean {
  for (const p of papers) if (p.figure_state?.kind === 'off') return false;
  return true;
}

/** Whether the worker reads figures: as it said (the `wanted` op's `figures_on`, one switch for
 *  the worker's life), or, until it has, as the papers op shows it (readsFigures). */
export function figuresOn(said: boolean | null | undefined, papers: Iterable<Figured>): boolean {
  return said ?? readsFigures(papers);
}

/** What the pane says of one figure (`outcome`, the charts op's), and the button it offers.
 *  **Read the figures** only for a figure not read (K14: never after a read is recorded, whose
 *  reader is deterministic); no button at all with the figures off (`figuresOn` false), which
 *  stop the work done unasked — a PDF dropped on an XML paper's figure is kept, not read; a PDF
 *  paper's figure takes no drop of its own (figuredrop.ts). */
/** PMC's settled answer in the pane: it had no PDF, or (M7's "PMC's PDF: ‹why›") it served one the
 *  gate did not keep — which "has no PDF" would contradict. */
function pmcSaid(note: string): string {
  const refused = /^PMC's PDF: (.*)$/s.exec(note);
  return refused ? `PMC’s PDF of it was not kept (${refused[1]}).` : `PMC has no PDF of it (${note}).`;
}

export function paneNote(outcome: FigureOutcome | null | undefined, paper: Figured | undefined, figuresOn: boolean): PaneNote {
  const none = (line: string | null): PaneNote => ({ line, button: null });
  if (!outcome) return none(null);
  const o = outcome.outcome;
  const xml = paper?.format === 'jats';
  if (!figuresOn && (o === 'no-pdf' || o === 'unread')) return none(`Figures are off (LITRAG_FIGURES=off): ${xml ? 'a PDF dropped here is kept, not read' : 'they are not read'}.`);
  const offer = (line: string | null, button: PaneNote['button']): PaneNote => ({ line, button: figuresOn ? button : null });
  const s = paper?.figure_state;
  const file = fileOf(paper);
  switch (o) {
    case 'no-pdf':
      if (s?.note) return offer(`${pmcSaid(s.note)} ${ADVICE}`, paper?.pmcid ? 'ask-again' : null);
      if (paper?.pmcid && s?.asked) return offer(`PMC could not be asked (${s.asked}). ${ADVICE}`, 'ask-again');
      if (paper?.pmcid) return offer(`No answer from PMC on record. ${ADVICE}`, 'ask');
      return none('Read from XML, which names its figures but holds none. A PDF of the paper draws them: drop one here, or fetch it with Collect PDFs on the Search tab, and its charts are read and pinned to these figures.');
    case 'unnumbered':
      return none('Its caption gives it no number (“Figure 2”), so no PDF’s charts can be pinned to it.');
    case 'no-plot':
      return none(
        xml
          ? `Its caption is${onPage(outcome.page)} ${file}, ${beside(paper)}; no plot the reader found on that page was pinned to it. A photograph, a diagram or a curve stays without numbers.`
          : 'Read: nothing in it is a chart the reader knows (bars, points). A photograph, a diagram or a curve stays without numbers.',
      );
    case 'no-caption': {
      const unmatched = s?.unmatched ?? 0;
      return none(`${file}, ${beside(paper)}, prints no caption numbered ${outcome.label}, so nothing in it was pinned here.${unmatched > 0 ? ` ${count(unmatched, 'plot')} in it sat under no caption of the paper’s.` : ''}`);
    }
    case 'small':
      return none('Smaller than an inch each way: not read for charts.');
    case 'unplaced':
      return none('No place on a page: not read for charts.');
    case 'same-number':
      return none(`Its caption’s number, ${outcome.label}, is an earlier figure’s too: a PDF’s plots for ${figureName(outcome.label)} are pinned to that one.`);
    case 'skipped':
      return none(outcome.detail);
    case 'unread':
      if (s?.kind === 'missing-file') return none(missingLine(paper)); // nothing on disk to read
      return offer(null, 'read');
    case 'plots':
      return none(xml ? `From ${outcome.page !== null ? `p. ${outcome.page} of ` : ''}${file}, ${beside(paper)}, pinned by its caption’s number.` : null);
    default: // unrecorded: read before what each figure came to was kept
      return none(null);
  }
}

/** The button a figure's pane draws for paneNote's `button`: its words, its hover text, what the
 *  activity bar says while it works, and what it asks of the worker (op and params, the caller adding
 *  the library) — Read the figures reads this paper's; Ask PMC asks for this paper's PDF alone, again
 *  whatever PMC said before (figures_fetch's `again`). None for no button. */
export interface PaneButton {
  label: string;
  title: string;
  doing: string;
  op: 'figures' | 'figures_fetch';
  params: Record<string, unknown>;
}

export function paneButton(button: PaneNote['button'], key: string): PaneButton | null {
  switch (button) {
    case 'read':
      return { label: 'Read the figures', title: 'Read the charts in this paper’s figures into numbers (bars, points, error bars), on this machine', doing: 'Reading the figures', op: 'figures', params: { keys: [key], force: true } };
    case 'ask':
    case 'ask-again':
      return {
        label: button === 'ask' ? 'Ask PMC for its PDF' : 'Ask PMC again',
        title: 'Ask PMC, by its PMCID, for this paper’s PDF: kept beside the XML if its first pages name the paper, and its figures read',
        doing: 'Asking PMC for its PDF',
        op: 'figures_fetch',
        params: { keys: [key], again: true },
      };
    default:
      return null;
  }
}

// ---- the drop overlay and the log ------------------------------------------------------------

/** The drop overlay's text: over a figure of a paper read from XML, the PDF dropped is kept for that paper's figures (figuredrop.ts). */
export function dropLabel(overFigure: boolean): string {
  return overFigure ? 'Drop to keep the PDF for this paper’s figures' : 'Drop to ingest';
}

/** The overlay's text for what lies under the drag (document.elementsFromPoint, topmost first). */
export function overlayText(under: Iterable<object>): string {
  return dropLabel(figuresTarget(under) !== null);
}

/** The log line a figures run's `done` says; the papers whose figures' outcomes it recorded, when it recorded any. */
export function figuresDone(done: Record<string, unknown>): string {
  const n = (k: string) => Number(done[k] ?? 0) || 0;
  const unmatched = n('unmatched');
  const recorded = n('recorded');
  return `figures: ${n('read')} of ${n('plots')} charts read in ${n('papers')} papers (plots in ${n('pinned')} of ${n('figures')} figures), ${n('values')} values${unmatched > 0 ? `, ${unmatched} plots under no caption` : ''}${recorded > 0 ? `, what each figure came to recorded for ${recorded} papers` : ''}`;
}

// ---- asking PMC for the PDFs -----------------------------------------------------------------

/** What PMC said when it had no PDF to give, after a Collect entry's title: " — PMC: ‹note›", or nothing when it has not said. */
export function pmcSuffix(note: string | null | undefined): string {
  return note ? ` — PMC: ${note}` : '';
}

/** The Search tab's button beside Collect PDFs: the papers read from XML whose figures PMC can be asked a PDF for (collectlist.ts, askable). */
export function askLabel(n: number): string {
  return `Ask PMC for ${n} figure PDF${n === 1 ? '' : 's'}`;
}

/** That button's hover text. */
export const ASK_TITLE = 'Ask PMC, by PMCID, for the PDF of each paper read from XML whose figures want one: a PDF whose first pages name the paper is kept beside its XML, and its figures are read';

/** What the activity bar says while it asks. */
export function askingLabel(n: number): string {
  return `Asking PMC for ${n} figure PDF${n === 1 ? '' : 's'}`;
}

/** The log line a figures_fetch's `done` says, once the asks are made and before the PDFs kept are
 *  read: one count per answer, the refused left out when there are none. `unreachable` is the ask
 *  that could not reach PMC and every paper passed by after it, that one ask counted in `asked`
 *  too: so PMC "could not be reached for" them, not "could not be asked". */
export function askedDone(done: Record<string, unknown>): string {
  const n = (k: string) => Number(done[k] ?? 0) || 0;
  const refused = n('refused');
  return `figures: asked PMC for ${n('asked')}, ${n('kept')} PDFs kept, ${n('missed')} with none${refused > 0 ? `, ${refused} whose PDF does not name the paper` : ''}, PMC could not be reached for ${n('unreachable')}`;
}

/** After a fetch: the papers it read from XML that still want a PDF for their figures, which the collect window walks when a person opens it (K15: it does not open by itself for them). */
export function fetchedFigures(n: number): string {
  return n === 1 ? '1 paper read from XML with no PDF for its figures: Collect PDFs walks it' : `${n} papers read from XML with no PDF for their figures: Collect PDFs walks them`;
}
