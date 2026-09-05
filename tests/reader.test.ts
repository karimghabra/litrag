/**
 * The reader verbs (#5): one paper whole in one call, and a question scoped
 * to one paper. Same fixture pipeline as the graph tests: Europe PMC XML in,
 * rows out, nothing touches the network.
 */

import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { attachNote, deleteNote, notesOf, openDb, paperPayload, replaceModelRows, sectionsOf, upsertPaper } from '../src/db.ts';
import { hashEmbedder } from '../src/embed.ts';
import { annotateLibrary, fetchCandidates, ingestLibrary } from '../src/ingest.ts';
import { createLibrary, type Library } from '../src/library.ts';
import { queryLibrary } from '../src/query.ts';
import { type Fetcher } from '../src/sources/europepmc.ts';

const fixture = (name: string) => readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), 'utf8');
const xml = fixture('PMC11278924.xml');
const fetcher: Fetcher = async (url) => {
  if (url.includes('fullTextXML')) return { ok: true, status: 200, text: async () => xml };
  if (url.includes('annotationsByArticleIds')) return { ok: true, status: 200, text: async () => fixture('europepmc-annotations.json') };
  return { ok: false, status: 404, text: async () => '' };
};
const embedder = hashEmbedder();
const now = '2026-09-05T05:00';
let root: string;
let lib: Library;
const key = 'doi:10.3390/mi15070851';

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

describe('lit paper', () => {
  it('answers one paper whole: row, sections in order, chunks, pinned mentions', () => {
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      const payload = paperPayload(db, key)!;
      expect(payload.paper.key).toBe(key);
      expect(payload.paper.doi).toBe('10.3390/mi15070851');
      expect(payload.paper.status).toBe('ingested');
      expect(payload.sections.length).toBeGreaterThan(3);
      expect(payload.sections.map((s) => s.ordinal)).toEqual([...payload.sections.map((s) => s.ordinal)].sort((a, b) => a - b));
      expect(payload.chunks.length).toBeGreaterThan(10);
      const sectionIds = new Set(payload.sections.map((s) => s.id));
      expect(payload.chunks.every((c) => sectionIds.has(c.section))).toBe(true);
      // Mentions name real entities and pin to this paper's chunks (or the whole paper).
      expect(payload.mentions.length).toBeGreaterThan(10);
      const chunkIds = new Set(payload.chunks.map((c) => c.id));
      expect(payload.mentions.every((m) => m.chunk === null || chunkIds.has(m.chunk))).toBe(true);
      expect(payload.mentions.map((m) => m.name.toLowerCase())).toContain('genipin');
    } finally {
      db.close();
    }
  });

  it('carries the model stage rows with their sections', () => {
    const db = openDb(lib.dbPath);
    try {
      const methods = sectionsOf(db, key).find((s) => s.kind === 'methods')!;
      replaceModelRows(db, key, 'ollama:test', [{ section: methods.id, rows: { claims: [{ text: 'Alignment survives crosslinking.', kind: 'finding' }], materials: [{ name: 'Genipin', role: 'crosslinker', amount: '0.1% w/v' }], methods: [{ name: 'Uniaxial tensile testing', description: 'Threads pulled to failure.' }], parameters: [] } }], now);
      const payload = paperPayload(db, key)!;
      expect(payload.claims).toEqual([expect.objectContaining({ section: methods.id, text: 'Alignment survives crosslinking.', kind: 'finding' })]);
      expect(payload.materials).toEqual([expect.objectContaining({ name: 'Genipin', role: 'crosslinker', amount: '0.1% w/v' })]);
      expect(payload.methods).toEqual([expect.objectContaining({ name: 'Uniaxial tensile testing' })]);
    } finally {
      db.close();
    }
  });

  it('answers nothing for a key the library does not hold', () => {
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      expect(paperPayload(db, 'doi:10.0000/not-here')).toBeUndefined();
    } finally {
      db.close();
    }
  });
});

describe('notes on chunks (#10)', () => {
  it('attaches to a chunk, rides the payload, and deletes by id', () => {
    const db = openDb(lib.dbPath);
    try {
      const chunk = (db.prepare('SELECT id FROM chunks WHERE paper = ? LIMIT 1').get(key) as { id: number }).id;
      const note = attachNote(db, key, chunk, 'The genipin concentration here matches our bench protocol.', now);
      expect(note.id).toBeGreaterThan(0);
      expect(note.quote.length).toBeGreaterThan(10);
      const payload = paperPayload(db, key)!;
      expect(payload.notes).toEqual([expect.objectContaining({ id: note.id, chunk, text: note.text })]);
      expect(deleteNote(db, note.id)).toBe(true);
      expect(paperPayload(db, key)!.notes).toEqual([]);
      expect(deleteNote(db, note.id)).toBe(false);
    } finally {
      db.close();
    }
  });

  it('carries an exact highlight as its quote, and refuses one from elsewhere (#11)', () => {
    const db = openDb(lib.dbPath);
    try {
      const row = db.prepare('SELECT id, text FROM chunks WHERE paper = ? LIMIT 1').get(key) as { id: number; text: string };
      const span = row.text.slice(20, 80);
      const note = attachNote(db, key, row.id, 'On this exact phrase.', now, span);
      expect(note.quote).toBe(span.trim());
      expect(() => attachNote(db, key, row.id, 'nope', now, 'words that never appear in the passage')).toThrow(/not in this passage/);
      deleteNote(db, note.id);
    } finally {
      db.close();
    }
  });

  it('refuses a chunk the paper does not hold', () => {
    const db = openDb(lib.dbPath);
    try {
      expect(() => attachNote(db, key, 999999, 'nope', now)).toThrow(/no chunk/);
    } finally {
      db.close();
    }
  });

  it('re-anchors by its quote when the chunk id dies, and survives at paper level when the passage is gone', () => {
    const db = openDb(lib.dbPath);
    try {
      const row = db.prepare('SELECT id, text FROM chunks WHERE paper = ? LIMIT 1').get(key) as { id: number; text: string };
      const note = attachNote(db, key, row.id, 'anchored', now);
      // A reread retires the id but keeps the prose: simulate with a dead id.
      db.prepare('UPDATE notes SET chunk = 999999 WHERE id = ?').run(note.id);
      const anchored = notesOf(db, key).find((n) => n.id === note.id)!;
      expect(anchored.chunk).toBe(row.id);
      // The passage itself gone: the note stays, at paper level.
      db.prepare("UPDATE notes SET chunk = 999999, quote = 'text that exists nowhere in this paper' WHERE id = ?").run(note.id);
      const unanchored = notesOf(db, key).find((n) => n.id === note.id)!;
      expect(unanchored.chunk).toBeNull();
      expect(unanchored.text).toBe('anchored');
      deleteNote(db, note.id);
    } finally {
      db.close();
    }
  });
});

describe('the facts pass (#9)', () => {
  it('leads with a parameter row when the question names one', async () => {
    const db = openDb(lib.dbPath);
    try {
      const methods = sectionsOf(db, key).find((s) => s.kind === 'methods')!;
      replaceModelRows(db, key, 'ollama:facts', [{ section: methods.id, rows: { claims: [], materials: [], methods: [], parameters: [{ entity: 'pH', value: '8.2', unit: 'pH unit', context: 'the isoelectric point of collagen sits near 8.2' }] } }], now);
    } finally {
      db.close();
    }
    const hits = await queryLibrary(lib, 'isoelectric point of collagen', embedder, { limit: 5 });
    const fact = hits.find((h) => h.ranks.facts);
    expect(fact).toBeDefined();
    expect(fact!.text).toContain('8.2');
    expect(fact!.citation.length).toBeGreaterThan(10);
  });

  it('says out loud when the library lacks a term', async () => {
    const hits = await queryLibrary(lib, 'zirconium nanoparticle coatings', embedder, { limit: 5 });
    expect(hits[0]!.kind).toBe('coverage');
    expect(hits[0]!.text).toContain('"zirconium"');
    expect(hits[0]!.text).toContain('lit search');
  });

  it('points across the hall when a sibling library covers the missing term', async () => {
    const empty = createLibrary(root, { name: 'Empty Shelf', now });
    openDb(empty.dbPath).close();
    const hits = await queryLibrary(empty, 'genipin crosslinking', embedder, { limit: 3 });
    expect(hits[0]!.kind).toBe('coverage');
    expect(hits[0]!.text).toContain('Reader pilot');
    expect(hits[0]!.text).toContain('Switch the library');
  });

  it('stays silent about coverage when every term is present', async () => {
    const hits = await queryLibrary(lib, 'genipin crosslinking', embedder, { limit: 5 });
    expect(hits.every((h) => h.kind !== 'coverage')).toBe(true);
  });
});

describe('query --paper', () => {
  it('keeps only the named paper\'s chunks', async () => {
    const hits = await queryLibrary(lib, 'genipin crosslinking', embedder, { limit: 5, paper: key });
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.every((h) => h.paper === key)).toBe(true);
  });

  it('answers empty, not wrongly, for a paper that is not there', async () => {
    const hits = await queryLibrary(lib, 'genipin crosslinking', embedder, { limit: 5, paper: 'doi:10.0000/not-here' });
    expect(hits).toEqual([]);
  });
});
