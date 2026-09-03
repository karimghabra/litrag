/**
 * The whole loop against a scratch root: a library made for a project, a
 * candidate staged, its text fetched from a fixture, read into rows, embedded
 * with the stand-in, and asked a question — with the store readable by SQL.
 */

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { openDb, paperByKey, statusView, upsertPaper } from '../src/db.ts';
import { hashEmbedder } from '../src/embed.ts';
import { fetchCandidates, ingestLibrary } from '../src/ingest.ts';
import { createLibrary, listLibraries, openLibrary, type Library } from '../src/library.ts';
import { queryLibrary, runSql } from '../src/query.ts';
import type { Fetcher } from '../src/sources/europepmc.ts';

const xml = readFileSync(fileURLToPath(new URL('./fixtures/PMC11278924.xml', import.meta.url)), 'utf8');
const fetcher: Fetcher = async (url) => {
  const ok = url.includes('PMC11278924/fullTextXML');
  return { ok, status: ok ? 200 : 404, text: async () => (ok ? xml : '') };
};
const embedder = hashEmbedder();
const now = '2026-09-02T09:00';

let root: string;
let lib: Library;

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'lit-'));
});
afterAll(() => {
  rmSync(root, { recursive: true, force: true });
});

describe('a library for a project', () => {
  it('is made once, found by id, project id or name, and refused twice', () => {
    lib = createLibrary(root, { name: 'Looped Ligament', projectId: 'n156', projectRef: 'looped-ligament', now });
    expect(lib.manifest.id).toBe('looped-ligament');
    expect(existsSync(lib.inboxDir)).toBe(true);
    expect(openLibrary(root, 'n156')?.manifest.id).toBe('looped-ligament');
    expect(openLibrary(root, 'looped ligament')?.manifest.id).toBe('looped-ligament');
    expect(openLibrary(root, 'looped-ligament')?.manifest.id).toBe('looped-ligament');
    expect(openLibrary(root, 'nope')).toBeNull();
    expect(() => createLibrary(root, { name: 'Looped Ligament again', projectId: 'n156' })).toThrow(/already exists/);
    expect(listLibraries(root)).toHaveLength(1);
  });

  it('writes its manifest the same way twice', () => {
    const first = readFileSync(lib.manifestPath, 'utf8');
    const again = createLibrary(root, { name: 'Twin check', now });
    rmSync(again.dir, { recursive: true, force: true });
    expect(readFileSync(lib.manifestPath, 'utf8')).toBe(first);
  });
});

describe('the pipeline', () => {
  const key = 'doi:10.3390/mi15070851';

  it('stages a candidate once, however many times it is seen', () => {
    const db = openDb(lib.dbPath);
    try {
      const first = upsertPaper(db, { doi: '10.3390/MI15070851', pmcid: 'PMC11278924', title: 'Untitled (staged by hand)', source: 'europepmc', openAccess: true }, now);
      expect(first).toEqual({ key, created: true });
      const again = upsertPaper(db, { doi: '10.3390/mi15070851', title: 'Untitled (staged by hand)', year: 2024, source: 'europepmc' }, now);
      expect(again).toEqual({ key, created: false });
      expect(paperByKey(db, key)?.year).toBe(2024);
      upsertPaper(db, { pmid: '1', title: 'Paywalled paper with no PMC text', source: 'europepmc' }, now);
    } finally {
      db.close();
    }
  });

  it('fetches full text where there is any, and names what needs a PDF', async () => {
    const report = await fetchCandidates(lib, { fetcher, now });
    expect(report.fetched).toEqual([key]);
    expect(report.needsPdf).toEqual(['pmid:1']);
    const db = openDb(lib.dbPath);
    try {
      const paper = paperByKey(db, key)!;
      expect(paper.status).toBe('fetched');
      expect(existsSync(join(lib.papersDir, paper.file!))).toBe(true);
      expect(paperByKey(db, 'pmid:1')?.status).toBe('needs-pdf');
    } finally {
      db.close();
    }
  });

  it('reads the paper into rows, embeds every chunk, and is idempotent', async () => {
    const report = await ingestLibrary(lib, embedder, { now });
    expect(report.ingested).toEqual([key]);
    expect(report.failed).toEqual([]);
    expect(report.embedded).toBeGreaterThan(10);
    const db = openDb(lib.dbPath);
    try {
      const status = statusView(db, embedder.model);
      expect(status.papers).toEqual({ candidate: 0, fetched: 0, 'needs-pdf': 1, ingested: 1 });
      expect(status.vectors).toBe(status.chunks);
      expect(status.parameters).toBeGreaterThan(20);
      // A placeholder title gives way to the paper's own; a real one would stand.
      expect(paperByKey(db, key)?.title).toContain('Aligned Collagen');
      expect(paperByKey(db, key)?.pmid).toBe('39064362');
    } finally {
      db.close();
    }
    const again = await ingestLibrary(lib, embedder, { now });
    expect(again.ingested).toEqual([]);
    expect(again.embedded).toBe(0);
    // --reread reads the paper on disk again and remakes its vectors.
    const reread = await ingestLibrary(lib, embedder, { now, reread: true });
    expect(reread.ingested).toEqual([key]);
    expect(reread.embedded).toBe(report.embedded);
  });

  it('answers a question with chunks that cite the paper and the section', async () => {
    const hits = await queryLibrary(lib, 'How does crosslinking degree change the tensile modulus of aligned collagen threads?', embedder, { limit: 5 });
    expect(hits.length).toBe(5);
    expect(hits[0]!.paper).toBe(key);
    expect(hits[0]!.citation).toContain('Aligned Collagen');
    expect(hits[0]!.citation).toContain('(2024)');
    expect(hits[0]!.citation).toContain('doi:10.3390/mi15070851');
    expect(hits[0]!.section.length).toBeGreaterThan(0);
    expect(hits.some((h) => h.ranks.words)).toBe(true);
    expect(hits.some((h) => h.ranks.meaning)).toBe(true);
    expect(hits.every((h) => /crosslink|modulus|tensile/i.test(h.text))).toBe(true);
  });

  it('is a store a person can SELECT from, and only SELECT', () => {
    const kinds = runSql(lib, 'select kind, count(*) as n from parameters group by kind order by n desc');
    expect(kinds.columns).toEqual(['kind', 'n']);
    expect(kinds.rows.some((r) => r['kind'] === 'concentration')).toBe(true);
    const mM = runSql(lib, "select value, unit, sentence from parameters where unit = 'mM'");
    expect(mM.rows.length).toBeGreaterThan(0);
    expect(() => runSql(lib, 'delete from papers')).toThrow(/SELECT/);
    expect(runSql(lib, "select count(*) as n from papers where title like '%;%'").rows[0]).toEqual({ n: 0 });
    expect(() => runSql(lib, 'select 1; drop table papers')).toThrow(/SELECT/);
    expect(runSql(lib, 'select count(*) as n from papers').rows[0]).toEqual({ n: 2 });
  });

  it('keeps a paper it cannot read as fetched, named in the report, and carries on', async () => {
    writeFileSync(join(lib.inboxDir, 'not-really.pdf'), 'this is not a pdf');
    const report = await ingestLibrary(lib, embedder, { now });
    expect(report.inbox).toHaveLength(1);
    expect(report.failed).toHaveLength(1);
    expect(report.failed[0]!.key).toBe(report.inbox[0]);
    expect(existsSync(join(lib.inboxDir, 'not-really.pdf'))).toBe(false);
    const db = openDb(lib.dbPath);
    try {
      expect(paperByKey(db, report.inbox[0]!)?.status).toBe('fetched');
    } finally {
      db.close();
    }
  });
});

describe('the shared spine', () => {
  it('lets an empty library answer from the ones it includes, each hit labelled', async () => {
    const spine = createLibrary(root, { name: 'Meniscus', includes: ['looped-ligament'], now });
    openDb(spine.dbPath).close();
    const hits = await queryLibrary(spine, 'crosslinking degree and modulus', embedder, { limit: 3 });
    expect(hits).toHaveLength(3);
    expect(hits.every((h) => h.library === 'looped-ligament')).toBe(true);
    expect(await queryLibrary(spine, 'crosslinking degree and modulus', embedder, { limit: 3, spine: false })).toEqual([]);
  });
});
