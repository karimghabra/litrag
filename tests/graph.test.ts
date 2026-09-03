/**
 * The graph layer: entities from Europe PMC's terms and from the model stage,
 * a walk from the question's entities, and what that adds to the ranking.
 */

import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { openDb, replaceModelRows, sectionsOf, statusView, upsertPaper } from '../src/db.ts';
import { BGE_QUERY_PREFIX, hashEmbedder } from '../src/embed.ts';
import { buildGraph, entitySeeds, graphSearch, graphStats, personalizedPageRank } from '../src/graph.ts';
import { annotateLibrary, fetchCandidates, ingestLibrary } from '../src/ingest.ts';
import { createLibrary, type Library } from '../src/library.ts';
import { queryLibrary } from '../src/query.ts';
import { annotationsFor, type Fetcher } from '../src/sources/europepmc.ts';

const fixture = (name: string) => readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), 'utf8');
const xml = fixture('PMC11278924.xml');
const fetcher: Fetcher = async (url) => {
  if (url.includes('fullTextXML')) return { ok: true, status: 200, text: async () => xml };
  if (url.includes('annotationsByArticleIds')) return { ok: true, status: 200, text: async () => fixture('europepmc-annotations.json') };
  return { ok: false, status: 404, text: async () => '' };
};
const embedder = hashEmbedder();
const now = '2026-09-03T05:00';
let root: string;
let lib: Library;
const key = 'doi:10.3390/mi15070851';

beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'lit-graph-'));
  lib = createLibrary(root, { name: 'Graph pilot', now });
  const db = openDb(lib.dbPath);
  upsertPaper(db, { doi: '10.3390/mi15070851', pmcid: 'PMC11278924', title: 'Untitled (staged)', source: 'europepmc', pubType: 'research-article; journal article' }, now);
  db.close();
  await fetchCandidates(lib, { fetcher, now });
  await ingestLibrary(lib, embedder, { now });
});
afterAll(() => rmSync(root, { recursive: true, force: true }));

describe('Europe PMC terms', () => {
  it('read as typed entities with the section they sit in', async () => {
    const terms = await annotationsFor('PMC11278924', fetcher);
    expect(terms.length).toBeGreaterThan(100);
    const genipin = terms.filter((t) => t.exact.toLowerCase() === 'genipin');
    expect(genipin.length).toBeGreaterThan(10);
    expect(genipin[0]!.type).toBe('Chemicals');
    expect(new Set(terms.map((t) => t.section))).toContain('Methods');
  });

  it('become entity nodes pinned to the chunks that name them, once', async () => {
    const report = await annotateLibrary(lib, { fetcher, now });
    expect(report.annotated).toEqual([key]);
    expect(report.mentions).toBeGreaterThan(20);
    const db = openDb(lib.dbPath);
    try {
      const status = statusView(db, embedder.model);
      expect(status.annotated).toBe(1);
      expect(status.entities).toBeGreaterThan(15);
      const genipin = db.prepare("SELECT COUNT(*) n FROM mentions m JOIN entities e ON e.id = m.entity WHERE e.norm = 'genipin' AND m.chunk IS NOT NULL").get() as { n: number };
      expect(genipin.n).toBeGreaterThan(3);
      expect((db.prepare('SELECT pub_type FROM papers WHERE key = ?').get(key) as { pub_type: string }).pub_type).toContain('research-article');
    } finally {
      db.close();
    }
    const again = await annotateLibrary(lib, { fetcher, now });
    expect(again.annotated).toEqual([]);
  });
});

describe('the walk', () => {
  it('builds papers, chunks, entities and edges from the tables, hubs left out', () => {
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      const graph = buildGraph(db);
      const stats = graphStats(db, graph);
      expect(stats.papers).toBe(1);
      expect(stats.chunks).toBeGreaterThan(10);
      expect(stats.mentionEdges).toBeGreaterThan(10);
      // One paper: nothing can be a hub (the rule needs more than three papers).
      expect(stats.hubsDropped).toBe(0);
      const seeds = entitySeeds(graph, 'How much genipin was used, and at what ethanol concentration?');
      const names = [...seeds.keys()].map((i) => graph.entityNames.get(i));
      expect(names).toEqual(expect.arrayContaining(['genipin', 'ethanol']));
      // Specificity: ethanol is named in fewer passages than genipin, so it weighs more.
      const weight = (name: string) => seeds.get(graph.entityByNorm.get(name)!)!;
      expect(weight('ethanol')).toBeGreaterThan(weight('genipin'));
      expect(entitySeeds(graph, 'nothing named here').size).toBe(0);
    } finally {
      db.close();
    }
  });

  it('settles on the passages that name the question\'s entities', () => {
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      const graph = buildGraph(db);
      const { hits, seeds } = graphSearch(graph, 'genipin crosslinking of aligned collagen threads', [], 5);
      expect(seeds).toContain('genipin');
      expect(hits.length).toBe(5);
      const texts = db.prepare('SELECT id, text FROM chunks').all() as { id: number; text: string }[];
      const top = texts.find((t) => t.id === hits[0]!.chunk)!;
      expect(top.text.toLowerCase()).toContain('genipin');
      // A walk with no seeds goes nowhere, and says so.
      expect(graphSearch(graph, 'nothing named', [], 5)).toEqual({ hits: [], seeds: [] });
    } finally {
      db.close();
    }
  });

  it('is a proper personalized PageRank: mass sums to one and stays near the seeds', () => {
    const graph = { index: new Map([['a', 0], ['b', 1], ['c', 2], ['d', 3]]), ids: ['a', 'b', 'c', 'd'], adj: [[[1, 1]], [[0, 1], [2, 1]], [[1, 1], [3, 1]], [[2, 1]]] as [number, number][][], chunkOf: new Map(), entityByNorm: new Map(), entityNames: new Map(), entitySpread: new Map() };
    const r = personalizedPageRank(graph, new Map([[0, 1]]));
    const total = Array.from(r).reduce((s, x) => s + x, 0);
    expect(total).toBeCloseTo(1, 6);
    expect(r[0]).toBeGreaterThan(r[1]!);
    expect(r[1]).toBeGreaterThan(r[2]!);
    expect(r[2]).toBeGreaterThan(r[3]!);
  });

  it('adds a third ranking to the fusion, and can be switched off', async () => {
    const question = 'ethanol dehydration of crosslinked threads';
    const trace = { seeds: {} as Record<string, string[]> };
    const withGraph = await queryLibrary(lib, question, embedder, { limit: 5, trace });
    expect(trace.seeds['graph-pilot']).toContain('ethanol');
    expect(withGraph.some((h) => h.ranks.graph)).toBe(true);
    const without = await queryLibrary(lib, question, embedder, { limit: 5, graph: false });
    expect(without.every((h) => h.ranks.graph === undefined)).toBe(true);
  });
});

describe('the model stage feeds the graph', () => {
  it('turns materials and methods into entities pinned to their sections', () => {
    const db = openDb(lib.dbPath);
    try {
      const sections = sectionsOf(db, key);
      const methods = sections.find((s) => s.kind === 'methods')!;
      replaceModelRows(db, key, 'ollama:test', [{ section: methods.id, rows: { claims: [], materials: [{ name: 'Genipin', role: 'crosslinker', amount: '0.1% w/v' }, { name: 'Bovine collagen type I', role: 'scaffold material' }], methods: [{ name: 'Uniaxial tensile testing', description: 'Threads pulled to failure.' }], parameters: [] } }], now);
      const rows = db.prepare("SELECT e.name, e.kind, m.source, m.chunk FROM mentions m JOIN entities e ON e.id = m.entity WHERE m.source = 'model' ORDER BY e.name").all() as { name: string; kind: string; source: string; chunk: number | null }[];
      expect(rows.map((r) => [r.name, r.kind])).toEqual(expect.arrayContaining([['Genipin', 'material'], ['Bovine collagen type I', 'material'], ['Uniaxial tensile testing', 'method']]));
      // "Genipin" from the model and "genipin" from Europe PMC are one node.
      const genipin = db.prepare("SELECT COUNT(*) n FROM entities WHERE norm = 'genipin'").get() as { n: number };
      expect(genipin.n).toBe(2); // material (model) and chemicals (europepmc) are different kinds, deliberately
      expect(rows.every((r) => r.chunk !== null)).toBe(true);
    } finally {
      db.close();
    }
  });
});

describe('a question, embedded', () => {
  it('carries the BGE instruction only for BGE models', async () => {
    const plain = hashEmbedder();
    const q = await plain.embedQuery('genipin');
    const [t] = await plain.embed(['genipin']);
    expect(Array.from(q)).toEqual(Array.from(t!));
    expect(BGE_QUERY_PREFIX.startsWith('Represent this sentence')).toBe(true);
  });
});
