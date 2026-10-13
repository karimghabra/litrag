/**
 * What the collect window walks (app/src/main/collect.ts): the candidates no open copy was found
 * for, then the XML papers whose figures want a PDF — the XML is the paper's text, and the PDF a
 * person fetches for it goes to that paper by its key, and is kept beside it to read its charts
 * when its first pages name the paper (parser/litrag_parser/figures.py). Beside its button, the
 * Search tab's Ask PMC (the worker's figures_fetch) and what a fetch goes on to: each decided here,
 * apart from the DOM (search.ts acts on it).
 */

import { ASK_TITLE, askLabel, pmcSuffix } from './figstate.ts';

export interface WantedCandidate {
  cand_id: number;
  title?: string | null;
  doi?: string | null;
  pmid?: string | null;
  pmcid?: string | null;
  /** an open copy OpenAlex knows of */
  oa_url?: string | null;
}

/** an XML paper with numbered figures and no PDF beside it (figures.figures_wanted) */
export interface FiguresWanted {
  key: string;
  title: string | null;
  doi: string | null;
  pmid: string | null;
  pmcid: string | null;
  figures: number;
  /** PMC's settled answer when it was asked for the PDF and had none to give */
  figures_note?: string | null;
  /** the open copy OpenAlex named for the candidate the paper was filed from */
  oa_url?: string | null;
}

export interface CollectEntry {
  cand_id: number;
  title: string | null;
  doi: string | null;
  pmid: string | null;
  pmcid: string | null;
  open?: string | null;
  /** the XML paper whose figures the PDF is for: the caught file goes to it, not asked what it is */
  paper?: string | null;
}

/** The XML papers wanting a PDF for their figures that the collect window can walk: those with an
 *  identifier to open a page by (a DOI, a PMID or a PMCID; collect.ts, linkFor). A paper filed by
 *  its file's hash has none, and takes a PDF dropped on one of its figures instead. */
export function walkable(figures: FiguresWanted[]): FiguresWanted[] {
  return figures.filter((f) => f.doi || f.pmid || f.pmcid);
}

/** How many of them PMC can be asked a PDF for (the worker's figures_fetch): those with a PMCID
 *  and no settled answer from PMC yet; one it answered is asked again from its figure's pane. */
export function askable(figures: FiguresWanted[]): number {
  return figures.filter((f) => f.pmcid && !f.figures_note).length;
}

/** The papers in the order the window opens them: those with no copy at all first, then those
 *  wanting only their figures that have a page to open (walkable) — an open copy OpenAlex named
 *  for one opened first, as a candidate's is, and what PMC said when it had none after its title. */
export function collectList(candidates: WantedCandidate[], figures: FiguresWanted[]): CollectEntry[] {
  const out: CollectEntry[] = candidates.map((c) => ({ cand_id: c.cand_id, title: c.title ?? null, doi: c.doi ?? null, pmid: c.pmid ?? null, pmcid: c.pmcid ?? null, open: c.oa_url ?? null }));
  for (const f of walkable(figures)) {
    out.push({
      cand_id: 0,
      title: `${f.title ?? f.key} — the PDF, for its ${f.figures} figure${f.figures === 1 ? '' : 's'}${pmcSuffix(f.figures_note)}`,
      doi: f.doi,
      pmid: f.pmid,
      pmcid: f.pmcid,
      open: f.oa_url ?? null,
      paper: f.key,
    });
  }
  return out;
}

/** The button's words: how many want a PDF, and how many want one for their figures. */
export function collectLabel(wanting: number, figures: number, collecting: boolean): string {
  if (collecting) return 'Collecting…';
  if (!wanting && !figures) return 'Collect PDFs';
  if (!figures) return `Collect PDFs (${wanting})`;
  return wanting ? `Collect PDFs (${wanting} + ${figures} for figures)` : `Collect PDFs (${figures} for figures)`;
}

/** Its hover text: the papers it walks, and those wanting a PDF for their figures that it cannot,
 *  with no identifier to open a page by (walkable). */
export function collectTitle(wanting: number, figures: FiguresWanted[]): string {
  const walked = walkable(figures).length;
  const none = figures.length - walked;
  const has = none === 1 ? 'has' : 'have';
  if (walked) return `Open each paper's page to fetch its PDF by hand: ${wanting} with no open copy, and ${walked} read from XML whose figures need the PDF to be read${none ? ` (${none} more ${has} no identifier to open a page by)` : ''}`;
  return `Open each paper’s page to fetch its PDF by hand${none ? `: ${none} read from XML whose figures need the PDF ${has} no identifier to open a page by` : ''}`;
}

/** The Search tab's Ask PMC, beside Collect PDFs: how many PMC can be asked a PDF for (askable);
 *  disabled at none and while an ask it sent is under way, and hidden with the figures off — but
 *  shown until the worker has said (`figuresOn` null), as a figure's pane takes them on. */
export function askButton(figures: FiguresWanted[], figuresOn: boolean | null, asking: boolean): { label: string; title: string; hidden: boolean; disabled: boolean } {
  const n = askable(figures);
  return { label: askLabel(n), title: ASK_TITLE, hidden: figuresOn === false, disabled: !n || asking };
}

/** The Search tab's ask of PMC (figures_fetch, with no keys), one at a time: under way from the
 *  click until that run's own `done` or `error`, told from a figure pane's ask (figures_fetch too,
 *  for its one paper) by the request's id. The id comes with the worker's `queued`, and a run with
 *  nothing left to ask can end before that answer is back, so a run that ends while it is on its
 *  way is remembered. */
export function askRun() {
  let run: { lib: string; id: string | null } | null = null;
  const early = new Set<string>();
  return {
    /** the project PMC is being asked for, or null */
    get lib(): string | null {
      return run?.lib ?? null;
    },
    start(lib: string): void {
      run = { lib, id: null };
      early.clear();
    },
    /** the worker queued it: whether it is still under way (not when it ended already, or no id came) */
    queued(id: unknown): boolean {
      if (!run) return false;
      const ours = typeof id === 'string' ? id : '';
      if (!ours || early.has(ours)) run = null;
      else run.id = ours;
      early.clear();
      return run !== null;
    },
    /** a figures_fetch ended, its `done` or its `error`: whether it was this ask, now over */
    ended(id: unknown): boolean {
      if (!run) return false;
      if (run.id === null) {
        if (typeof id === 'string') early.add(id);
        return false;
      }
      if (id !== run.id) return false;
      run = null;
      return true;
    },
    /** the request itself failed: nothing is under way */
    failed(): void {
      run = null;
      early.clear();
    },
  };
}

/** a fetch's `done`, one entry a candidate (acquire.py, fetch_one) */
export interface Fetched {
  cand_id?: number;
  status?: string;
}

/** What a fetch's `done` goes on to, once the candidates are listed again: one that left papers
 *  with no open copy opens the collect window for them (`open`), unless it is open already; else
 *  `figures` counts the papers this fetch read from XML that still want a PDF for their figures and
 *  that the window can walk (PMC, asked as each was filed, had none) — for the log, since the window
 *  does not open by itself for those (K15), and Collect PDFs counts them. */
export function afterFetch(
  fetched: Fetched[],
  candidates: { cand_id: number; paper_key?: string | null }[],
  figures: FiguresWanted[],
  collecting: boolean,
): { open: number } | { figures: number } {
  const open = fetched.filter((f) => f.status === 'needs-pdf').length;
  if (open && !collecting) return { open };
  const ids = new Set(fetched.map((f) => Number(f.cand_id)));
  const keys = new Set(candidates.filter((c) => ids.has(c.cand_id) && c.paper_key).map((c) => c.paper_key));
  return { figures: walkable(figures).filter((f) => keys.has(f.key)).length };
}
