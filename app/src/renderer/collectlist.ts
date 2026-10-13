/**
 * What the collect window walks (app/src/main/collect.ts): the candidates no open copy was found
 * for, then the XML papers whose figures want a PDF — the XML is the paper's text, and the PDF a
 * person fetches for it goes to that paper by its key, and is kept beside it to read its charts
 * when its first pages name the paper (parser/litrag_parser/figures.py).
 */

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

/** The papers in the order the window opens them: those with no copy at all first, then those
 *  wanting only their figures, which need a DOI or a PMID to have a page to open. */
export function collectList(candidates: WantedCandidate[], figures: FiguresWanted[]): CollectEntry[] {
  const out: CollectEntry[] = candidates.map((c) => ({ cand_id: c.cand_id, title: c.title ?? null, doi: c.doi ?? null, pmid: c.pmid ?? null, pmcid: c.pmcid ?? null, open: c.oa_url ?? null }));
  for (const f of figures) {
    if (!f.doi && !f.pmid && !f.pmcid) continue;
    out.push({ cand_id: 0, title: `${f.title ?? f.key} — the PDF, for its ${f.figures} figure${f.figures === 1 ? '' : 's'}`, doi: f.doi, pmid: f.pmid, pmcid: f.pmcid, paper: f.key });
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
