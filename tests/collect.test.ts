/**
 * Collect mode's plan: what the one window walks, in what order, and where
 * a caught download lands. The window itself is Electron's and is not
 * spawned here; the plan is the part with rules worth pinning.
 */

import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { collectJob, inboxFileFor } from '../src/collect.ts';
import { openDb, upsertPaper } from '../src/db.ts';
import { reuniteNamedStrays, takeInbox } from '../src/ingest.ts';
import { createLibrary, type Library } from '../src/library.ts';

const now = '2026-09-04T09:00';
let root: string;
let lib: Library;

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'lit-collect-'));
  lib = createLibrary(root, { name: 'Collect pilot', now });
  const db = openDb(lib.dbPath);
  try {
    upsertPaper(db, { doi: '10.1016/j.biomaterials.2011.11.066', title: 'Tenogenic differentiation', year: 2012, citedByCount: 159, source: 'europepmc' }, now);
    upsertPaper(db, { doi: '10.1016/j.jmbbm.2012.06.012', title: 'Genipin crosslinking', year: 2012, citedByCount: 62, source: 'europepmc' }, now);
    upsertPaper(db, { pmid: '12345678', title: 'PubMed-only paper', year: 2015, citedByCount: 3, source: 'europepmc' }, now);
    upsertPaper(db, { doi: '10.9999/ingested.already', title: 'Already read', year: 2020, citedByCount: 500, source: 'europepmc' }, now);
    db.prepare("UPDATE papers SET status = 'needs-pdf' WHERE doi != '10.9999/ingested.already' OR doi IS NULL").run();
    db.prepare("UPDATE papers SET status = 'ingested' WHERE doi = '10.9999/ingested.already'").run();
  } finally {
    db.close();
  }
});
afterAll(() => rmSync(root, { recursive: true, force: true }));

describe('the plan', () => {
  it('walks only needs-pdf papers, most-cited first, with a page per paper', () => {
    const job = collectJob(lib);
    expect(job.library).toBe('collect-pilot');
    expect(job.inboxDir).toBe(lib.inboxDir);
    expect(job.papers.map((p) => p.citedBy)).toEqual([159, 62, 3]);
    expect(job.papers[0]!.link).toBe('https://doi.org/10.1016/j.biomaterials.2011.11.066');
    expect(job.papers[2]!.link).toBe('https://pubmed.ncbi.nlm.nih.gov/12345678/');
    expect(job.unlinked).toBe(0);
  });

  it('names the caught file by the paper key, the way papers/ is named', () => {
    expect(inboxFileFor('doi:10.1016/j.jmbbm.2012.06.012')).toBe('doi_10.1016_j.jmbbm.2012.06.012.pdf');
    expect(inboxFileFor('pmid:12345678')).toBe('pmid_12345678.pdf');
    const job = collectJob(lib);
    for (const p of job.papers) expect(p.file).toMatch(/^[a-zA-Z0-9._-]+\.pdf$/);
  });
});

describe('a caught file comes home by its name', () => {
  it('is filed under the paper it was caught for, even with no DOI on its pages', () => {
    const db = openDb(lib.dbPath);
    try {
      writeFileSync(join(lib.inboxDir, inboxFileFor('doi:10.1016/j.jmbbm.2012.06.012')), 'no doi in these bytes');
      const taken = takeInbox(lib, db, now, () => {});
      expect(taken).toEqual(['doi:10.1016/j.jmbbm.2012.06.012']);
      const row = db.prepare('SELECT status, file FROM papers WHERE key = ?').get('doi:10.1016/j.jmbbm.2012.06.012') as { status: string; file: string };
      expect(row.status).toBe('fetched');
      expect(row.file).toBe('doi_10.1016_j.jmbbm.2012.06.012.pdf');
      expect(db.prepare("SELECT COUNT(*) n FROM papers WHERE key LIKE 'sha:%'").get()).toEqual({ n: 0 });
    } finally {
      db.close();
    }
  });

  it('reunites a stray an earlier ingest filed by its bytes', () => {
    const db = openDb(lib.dbPath);
    try {
      const name = inboxFileFor('pmid:12345678');
      const { key: strayKey } = upsertPaper(db, { title: `Untitled (${name})`, source: 'inbox' }, now, 'feedbeef'.repeat(8));
      db.prepare("UPDATE papers SET file = ?, sha256 = ?, status = 'ingested' WHERE key = ?").run('sha_feedbeef.pdf', 'feedbeef'.repeat(8), strayKey);
      const reunited = reuniteNamedStrays(lib, db, () => {});
      expect(reunited).toEqual(['pmid:12345678']);
      const owner = db.prepare('SELECT status, file FROM papers WHERE key = ?').get('pmid:12345678') as { status: string; file: string };
      expect(owner.status).toBe('fetched');
      expect(owner.file).toBe('sha_feedbeef.pdf');
      expect(db.prepare('SELECT COUNT(*) n FROM papers WHERE key = ?').get(strayKey)).toEqual({ n: 0 });
      // Nothing left to reunite: a second pass is a no-op.
      expect(reuniteNamedStrays(lib, db, () => {})).toEqual([]);
    } finally {
      db.close();
    }
  });

  it('drops a stray that is a second copy of a paper already filed', () => {
    const db = openDb(lib.dbPath);
    try {
      const name = inboxFileFor('pmid:12345678'); // filed by the reunion above
      const { key: strayKey } = upsertPaper(db, { title: `Untitled (${name})`, source: 'inbox' }, now, 'cafebabe'.repeat(8));
      db.prepare("UPDATE papers SET file = 'sha_cafebabe.pdf', sha256 = ?, status = 'ingested' WHERE key = ?").run('cafebabe'.repeat(8), strayKey);
      expect(reuniteNamedStrays(lib, db, () => {})).toEqual([]);
      expect(db.prepare('SELECT COUNT(*) n FROM papers WHERE key = ?').get(strayKey)).toEqual({ n: 0 });
      const owner = db.prepare('SELECT file FROM papers WHERE key = ?').get('pmid:12345678') as { file: string };
      expect(owner.file).toBe('sha_feedbeef.pdf'); // untouched
    } finally {
      db.close();
    }
  });

  it('a second drop under a filed name replaces the file and reads again', () => {
    const db = openDb(lib.dbPath);
    try {
      const name = inboxFileFor('pmid:12345678');
      writeFileSync(join(lib.inboxDir, name), 'a better copy of the same paper');
      const taken = takeInbox(lib, db, now, () => {});
      expect(taken).toEqual(['pmid:12345678']);
      const owner = db.prepare('SELECT status, file FROM papers WHERE key = ?').get('pmid:12345678') as { status: string; file: string };
      expect(owner.status).toBe('fetched');
      expect(owner.file).toBe('pmid_12345678.pdf');
      expect(db.prepare("SELECT COUNT(*) n FROM papers WHERE key LIKE 'sha:%'").get()).toEqual({ n: 0 });
    } finally {
      db.close();
    }
  });
});
