/**
 * The profile stage (#14): each paper answered against the library's facet
 * schema — explicit rows where naive embedding only had vibes. Fake Ollama,
 * real pipeline, nothing touches the network.
 */

import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { openDb, paperPayload, papersToProfile, upsertPaper } from '../src/db.ts';
import { hashEmbedder } from '../src/embed.ts';
import { fetchCandidates, ingestLibrary, profileLibrary } from '../src/ingest.ts';
import { createLibrary, DEFAULT_FACETS, libraryFacets, type Library } from '../src/library.ts';
import { ollamaProfiler, parseProfile, type FetchLike } from '../src/ollama.ts';
import { queryLibrary } from '../src/query.ts';
import { type Fetcher } from '../src/sources/europepmc.ts';

const fixture = (name: string) => readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), 'utf8');
const xml = fixture('PMC11278924.xml');
const fetcher: Fetcher = async (url) => {
  if (url.includes('fullTextXML')) return { ok: true, status: 200, text: async () => xml };
  return { ok: false, status: 404, text: async () => '' };
};
const embedder = hashEmbedder();
const now = '2026-09-06T09:00';
let root: string;
let lib: Library;
const key = 'doi:10.3390/mi15070851';

/** A fake Ollama whose chat answers a canned profile; asks are recorded. */
const asked: string[] = [];
const fakeChat = (answers: unknown): FetchLike => async (url, init) => {
  if (url.includes('/api/chat')) {
    asked.push(String(init?.body ?? ''));
    return { ok: true, status: 200, text: async () => JSON.stringify({ message: { content: JSON.stringify(answers) } }) };
  }
  return { ok: false, status: 404, text: async () => '' };
};

beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'lit-profile-'));
  lib = createLibrary(root, { name: 'Profile pilot', now });
  const db = openDb(lib.dbPath);
  upsertPaper(db, { doi: '10.3390/mi15070851', pmcid: 'PMC11278924', title: 'Untitled (staged)', source: 'europepmc' }, now);
  db.close();
  await fetchCandidates(lib, { fetcher, now });
  await ingestLibrary(lib, embedder, { now });
});
afterAll(() => rmSync(root, { recursive: true, force: true }));

describe('the schema', () => {
  it('defaults to the tissue-engineering prefill and is the library\'s to edit', () => {
    expect(libraryFacets(lib.manifest)).toEqual(DEFAULT_FACETS);
    expect(DEFAULT_FACETS.map((f) => f.key)).toEqual(
      expect.arrayContaining(['model', 'scaffold', 'crosslinking', 'sterilization', 'culture-length']),
    );
    const custom = { ...lib.manifest, facets: [{ key: 'imaging-modality', ask: 'What imaging modality?' }] };
    expect(libraryFacets(custom)).toEqual([{ key: 'imaging-modality', ask: 'What imaging modality?' }]);
  });

  it('drops answers for facets the schema never asked about', () => {
    const rows = parseProfile(
      JSON.stringify({ answers: [
        { facet: 'crosslinking', value: 'genipin 0.1%', evidence: 'Crosslinked in genipin.' },
        { facet: 'invented', value: 'nonsense', evidence: '' },
        { facet: 'crosslinking', value: '', evidence: 'empty value dropped' },
      ] }),
      ['crosslinking', 'model'],
    );
    expect(rows).toEqual([{ facet: 'crosslinking', value: 'genipin 0.1%', evidence: 'Crosslinked in genipin.' }]);
  });
});

describe('the profile stage', () => {
  it('answers every facet per paper, whole-paper, and runs once per model', async () => {
    const answers = {
      answers: [
        { facet: 'crosslinking', value: 'genipin 0.1% w/v', evidence: 'Threads were crosslinked in 0.1% genipin.' },
        { facet: 'crosslinking', value: 'EDC 1.8 M', evidence: 'A second set was crosslinked in EDC.' },
        { facet: 'model', value: 'not reported', evidence: '' },
      ],
    };
    const profiler = ollamaProfiler(lib.manifest.ollama, fakeChat(answers));
    const report = await profileLibrary(lib, profiler, {});
    expect(report.profiled).toEqual([key]);
    expect(report.failed).toEqual([]);
    // The prompt carried the whole paper and every question.
    expect(asked[0]).toContain('crosslinking');
    expect(asked[0]).toContain('culture-length');
    const db = openDb(lib.dbPath, { readOnly: true });
    try {
      const payload = paperPayload(db, key)!;
      expect(payload.profile.filter((r) => r.facet === 'crosslinking').map((r) => r.value)).toEqual(
        ['EDC 1.8 M', 'genipin 0.1% w/v'].sort().length === 2 ? expect.arrayContaining(['genipin 0.1% w/v', 'EDC 1.8 M']) : [],
      );
    } finally {
      db.close();
    }
    // Idempotent: the same model reads nothing twice.
    const db2 = openDb(lib.dbPath);
    try {
      expect(papersToProfile(db2, profiler.model)).toEqual([]);
    } finally {
      db2.close();
    }
  });

  it('leads the facts pass with a profile answer', async () => {
    const hits = await queryLibrary(lib, 'crosslinking with genipin', embedder, { limit: 5 });
    const fact = hits.find((h) => h.ranks.facts);
    expect(fact).toBeDefined();
    expect(fact!.text).toContain('crosslinking: ');
    expect(fact!.kind).toBe('profile');
  });
});
