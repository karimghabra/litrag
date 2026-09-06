/**
 * The format-aware inbox (#16): a JATS XML dropped under a paper's name is
 * that paper, keeps its extension, and reads through the JATS parser — the
 * same road a fetched full text takes.
 */

import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { openDb, paperByKey, upsertPaper } from '../src/db.ts';
import { hashEmbedder } from '../src/embed.ts';
import { fileNameFor, ingestLibrary } from '../src/ingest.ts';
import { createLibrary, type Library } from '../src/library.ts';
import { queryLibrary } from '../src/query.ts';

const xml = readFileSync(fileURLToPath(new URL('./fixtures/PMC11278924.xml', import.meta.url)), 'utf8');
const now = '2026-09-06T12:00';
let root: string;
let lib: Library;
const key = 'doi:10.3390/mi15070851';

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'lit-inboxxml-'));
  lib = createLibrary(root, { name: 'Inbox XML', now });
  const db = openDb(lib.dbPath);
  upsertPaper(db, { doi: '10.3390/mi15070851', title: 'Staged, awaiting its XML', source: 'europepmc' }, now);
  db.prepare("UPDATE papers SET status = 'needs-pdf' WHERE key = ?").run(key);
  db.close();
});
afterAll(() => rmSync(root, { recursive: true, force: true }));

describe('a JATS XML in the inbox', () => {
  it('is the paper its name points to, and reads as sections rather than a broken PDF', async () => {
    mkdirSync(lib.inboxDir, { recursive: true });
    writeFileSync(join(lib.inboxDir, fileNameFor(key, '.xml')), xml);
    const report = await ingestLibrary(lib, hashEmbedder(), { now });
    expect(report.inbox).toEqual([key]);
    expect(report.ingested).toContain(key);
    expect(report.failed).toEqual([]);
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      const paper = paperByKey(db, key)!;
      expect(paper.status).toBe('ingested');
      expect(paper.file!.endsWith('.xml')).toBe(true);
      const sections = (db.prepare('SELECT COUNT(*) n FROM sections WHERE paper = ?').get(key) as { n: number }).n;
      expect(sections).toBeGreaterThan(3);
    } finally {
      db.close();
    }
  });
});

describe('hiding reviews (#17)', () => {
  it('leaves a review paper out of chunks and facts, and keeps it when asked normally', async () => {
    const db = openDb(lib.dbPath);
    db.prepare("UPDATE papers SET pub_type = 'review-article; journal article' WHERE key = ?").run(key);
    db.close();
    const withReviews = await queryLibrary(lib, 'genipin crosslinking', hashEmbedder(), { limit: 5 });
    expect(withReviews.some((h) => h.chunk > 0)).toBe(true);
    expect(withReviews.find((h) => h.chunk > 0)!.pubType).toContain('review');
    const without = await queryLibrary(lib, 'genipin crosslinking', hashEmbedder(), { limit: 5, excludeReviews: true });
    expect(without.every((h) => h.chunk <= 0)).toBe(true); // only coverage/facts leads, no review chunks
  });
});
