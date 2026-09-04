/**
 * Collect mode's plan: the wanted list as a person will click through it.
 *
 * The window itself is Electron's (`collect-main.cjs`), spawned by the CLI;
 * this module only decides what it walks. The click stays the user's:
 * nothing here signs in, solves a challenge, or fetches behind a login —
 * the window's session catches what a person chose to download.
 */

import { openDb } from './db.ts';
import { fileNameFor } from './ingest.ts';
import type { Library } from './library.ts';

export interface CollectPaper {
  key: string;
  title: string;
  year: number | null;
  citedBy: number;
  /** The page to open: doi.org, else PubMed. */
  link: string;
  /** Where a caught download lands, inside the inbox. */
  file: string;
}

export interface CollectJob {
  library: string;
  inboxDir: string;
  papers: CollectPaper[];
  /** Wanted papers with no DOI and no PMID: nothing to open, still wanted. */
  unlinked: number;
}

/** The inbox file for a paper key — ingest's own naming, so the caught file's name IS the paper's identity. */
export function inboxFileFor(key: string): string {
  return fileNameFor(key, '.pdf');
}

/** The wanted list, most-cited first, as `lit wanted` orders it. */
export function collectJob(lib: Library): CollectJob {
  const db = openDb(lib.dbPath, { readOnly: true });
  try {
    const rows = db
      .prepare("SELECT key, title, year, doi, pmid, cited_by_count FROM papers WHERE status = 'needs-pdf' ORDER BY cited_by_count DESC, year DESC")
      .all() as { key: string; title: string; year: number | null; doi: string | null; pmid: string | null; cited_by_count: number | null }[];
    const papers: CollectPaper[] = [];
    let unlinked = 0;
    for (const r of rows) {
      const link = r.doi ? `https://doi.org/${r.doi}` : r.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${r.pmid}/` : '';
      if (!link) {
        unlinked += 1;
        continue;
      }
      papers.push({ key: r.key, title: r.title, year: r.year, citedBy: r.cited_by_count ?? 0, link, file: inboxFileFor(r.key) });
    }
    return { library: lib.manifest.id, inboxDir: lib.inboxDir, papers, unlinked };
  } finally {
    db.close();
  }
}
