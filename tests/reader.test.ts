/**
 * The reader payload and the graph export — the two verbs the app's Research
 * tab renders from (projtracker #48; issues #5 and #3 here). Driven through
 * the real pipeline on the Europe PMC fixture, as the graph tests are.
 */

import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { openDb, upsertPaper } from '../src/db.ts';
import { hashEmbedder } from '../src/embed.ts';
import { graphExport } from '../src/graph.ts';
import { annotateLibrary, fetchCandidates, ingestLibrary } from '../src/ingest.ts';
import { createLibrary, type Library } from '../src/library.ts';
import { queryLibrary } from '../src/query.ts';
import { paperView } from '../src/reader.ts';
import type { Fetcher } from '../src/sources/europepmc.ts';

const fixture = (name: string) => readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), 'utf8');
const xml = fixture('PMC11278924.xml');
const fetcher: Fetcher = async (url) => {
  if (url.includes('fullTextXML')) return { ok: true, status: 200, text: async () => xml };
  if (url.includes('annotationsByArticleIds')) return { ok: true, status: 200, text: async () => fixture('europepmc-annotations.json') };
  return { ok: false, status: 404, text: async () => '' };
};
const embedder = hashEmbedder();
const now = '2026-09-05T05:00';
const key = 'doi:10.3390/mi15070851';
let root: string;
let lib: Library;

beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'lit-reader-'));
  lib = createLibrary(root, { name: 'Reader pilot', now });
  const db = openDb(lib.dbPath);
  upsertPaper(db, { doi: '10.3390/mi15070851', pmcid: 'PMC11278924', title: 'Untitled (staged)', source: 'europepmc', pubType: 'research-article; journal article' }, now);
  db.close();
  await fetchCandidates(lib, { fetcher, now });
  await ingestLibrary(lib, embedder, { now });
  await annotateLibrary(lib, { fetcher, now });
});
afterAll(() => rmSync(root, { recursive: true, force: true }));

describe('lit paper — the reader payload', () => {
  it('hands back the whole paper as rows: sections in order, chunks, pinned mentions', () => {
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      const view = paperView(db, key)!;
      expect(view).toBeDefined();
      expect(view.paper.key).toBe(key);
      expect(view.paper.status).toBe('ingested');

      // Sections arrive in printed order, and every chunk names a section
      // the payload also carries — the reader never joins across calls.
      expect(view.sections.length).toBeGreaterThan(2);
      expect(view.sections.map((s) => s.ordinal)).toEqual([...view.sections.map((s) => s.ordinal)].sort((a, b) => a - b));
      const sectionIds = new Set(view.sections.map((s) => s.id));
      expect(view.chunks.length).toBeGreaterThan(0);
      for (const chunk of view.chunks) expect(sectionIds.has(chunk.section)).toBe(true);

      // Mentions are pinned: each names an entity and (when chunk-level) a
      // chunk the payload holds, so highlighting is a lookup, not a query.
      expect(view.mentions.length).toBeGreaterThan(0);
      const chunkIds = new Set(view.chunks.map((c) => c.id));
      for (const m of view.mentions) {
        expect(m.name.length).toBeGreaterThan(0);
        if (m.chunk !== null) expect(chunkIds.has(m.chunk)).toBe(true);
      }
    } finally {
      db.close();
    }
  });

  it('answers undefined for a paper the library does not hold', () => {
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      expect(paperView(db, 'doi:10.1000/nope')).toBeUndefined();
    } finally {
      db.close();
    }
  });
});

describe('query --paper — the question scoped to one paper', () => {
  it('answers only from that paper', async () => {
    const hits = await queryLibrary(lib, 'collagen', embedder, { paper: key, limit: 5 });
    expect(hits.length).toBeGreaterThan(0);
    for (const hit of hits) expect(hit.paper).toBe(key);
  });

  it('answers nothing for a key the library does not hold, rather than leaking neighbours', async () => {
    const hits = await queryLibrary(lib, 'collagen', embedder, { paper: 'doi:10.1000/nope' });
    expect(hits).toEqual([]);
  });
});

describe('lit graph --json — the export', () => {
  it('exports paper and entity nodes with aggregated mention edges, hubs marked not dropped', () => {
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      const exported = graphExport(db);
      const papers = exported.nodes.filter((n) => n.type === 'paper');
      const entities = exported.nodes.filter((n) => n.type === 'entity');
      expect(papers.map((p) => p.id)).toContain(`paper:${key}`);
      expect(entities.length).toBeGreaterThan(0);

      // Every edge connects nodes the export carries, and mention edges
      // aggregate to one per entity-paper pair.
      const ids = new Set(exported.nodes.map((n) => n.id));
      const seen = new Set<string>();
      for (const edge of exported.edges) {
        expect(ids.has(edge.from)).toBe(true);
        expect(ids.has(edge.to)).toBe(true);
        if (edge.type === 'mention') {
          const pair = `${edge.from}→${edge.to}`;
          expect(seen.has(pair)).toBe(false);
          seen.add(pair);
          expect(edge.weight).toBeGreaterThan(0);
        }
      }

      // With one paper, nothing can span most papers: no hubs, but the
      // marker exists on every entity node for bigger libraries.
      for (const e of entities) expect(typeof e.hub).toBe('boolean');
    } finally {
      db.close();
    }
  });
});
