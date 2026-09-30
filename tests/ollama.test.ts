/**
 * The GPU path against a fake Ollama: what the tool sends, what it makes of
 * the answer, and the rows that land in the store.
 */

import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { openDb, statusView, upsertPaper } from '../src/db.ts';
import { hashEmbedder } from '../src/embed.ts';
import { extractLibrary, fetchCandidates, ingestLibrary, piecesOf } from '../src/ingest.ts';
import { createLibrary, openLibrary, type Library } from '../src/library.ts';
import { ollamaEmbedder, ollamaExtractor, ollamaHealth, parseRows, type FetchLike, type OllamaSettings } from '../src/ollama.ts';
import { runSql } from '../src/query.ts';

const settings: OllamaSettings = { url: 'http://127.0.0.1:11434', chat: 'qwen3:14b', embed: 'nomic-embed-text' };
const xml = readFileSync(fileURLToPath(new URL('./fixtures/PMC11278924.xml', import.meta.url)), 'utf8');

function fakeOllama(calls: { url: string; body: unknown }[] = []): FetchLike {
  return async (url, init) => {
    const body = init?.body ? (JSON.parse(init.body) as Record<string, unknown>) : undefined;
    calls.push({ url, body });
    const reply = (status: number, value: unknown) => ({ ok: status < 300, status, text: async () => JSON.stringify(value) });
    if (url.endsWith('/api/version')) return reply(200, { version: '0.12.0' });
    if (url.endsWith('/api/tags')) return reply(200, { models: [{ name: 'qwen3:14b' }, { name: 'nomic-embed-text:latest' }] });
    if (url.endsWith('/api/embed')) {
      const input = body?.['input'] as string[];
      return reply(200, { embeddings: input.map((t) => [t.length % 7, 1, 0.5]) });
    }
    if (url.endsWith('/api/chat')) {
      const user = (body?.['messages'] as { role: string; content: string }[]).find((m) => m.role === 'user')?.content ?? '';
      const rows = {
        claims: [{ text: `Something this section says (${user.split('\n')[1]})`, kind: 'finding' }],
        materials: [{ name: 'EDC', role: 'crosslinker', amount: '10 mM' }],
        methods: [{ name: 'Electrochemical alignment', description: 'Collagen aligned between electrodes.' }],
        parameters: [{ entity: 'EDC concentration', value: '10', unit: 'mM', context: '10 mM EDC' }],
        junk: 'ignored',
      };
      return reply(200, { message: { role: 'assistant', content: JSON.stringify(rows) } });
    }
    return reply(404, {});
  };
}

describe('talking to Ollama', () => {
  it('reports the server, its models, and what is missing', async () => {
    const health = await ollamaHealth(settings, ['qwen3:14b', 'nomic-embed-text', 'gemma3:12b'], fakeOllama());
    expect(health).toMatchObject({ reachable: true, version: '0.12.0', missing: ['gemma3:12b'] });
    const down = await ollamaHealth(settings, ['qwen3:14b'], async () => {
      throw new Error('ECONNREFUSED');
    });
    expect(down).toEqual({ reachable: false, models: [], missing: ['qwen3:14b'] });
  });

  it('embeds through /api/embed, in batches, as float32', async () => {
    const calls: { url: string; body: unknown }[] = [];
    const embedder = ollamaEmbedder(settings, fakeOllama(calls));
    const vectors = await embedder.embed(Array.from({ length: 40 }, (_, i) => `text ${i}`));
    expect(vectors).toHaveLength(40);
    expect(vectors[0]).toBeInstanceOf(Float32Array);
    expect(embedder.dims).toBe(3);
    expect(embedder.model).toBe('ollama:nomic-embed-text');
    expect(calls.map((c) => c.url)).toEqual(['http://127.0.0.1:11434/api/embed', 'http://127.0.0.1:11434/api/embed']);
    expect((calls[0]!.body as { model: string }).model).toBe('nomic-embed-text');
  });

  it('asks for rows against the schema, at temperature zero, thinking off', async () => {
    const calls: { url: string; body: unknown }[] = [];
    const extractor = ollamaExtractor(settings, fakeOllama(calls));
    const rows = await extractor.extract({ heading: 'Methods', text: 'Threads were crosslinked in 10 mM EDC.' }, { title: 'A paper' });
    const body = calls[0]!.body as Record<string, unknown>;
    expect(body['model']).toBe('qwen3:14b');
    expect(body['stream']).toBe(true); // streamed: a long generation must not starve the client's headers timeout
    expect(body['think']).toBe(false);
    expect(body['format']).toMatchObject({ type: 'object' });
    expect((body['options'] as { temperature: number }).temperature).toBe(0);
    expect(rows.materials).toEqual([{ name: 'EDC', role: 'crosslinker', amount: '10 mM' }]);
    expect(rows.parameters[0]).toMatchObject({ entity: 'EDC concentration', unit: 'mM' });
  });

  it('keeps what fits the shape and drops what does not', () => {
    const rows = parseRows(JSON.stringify({
      claims: [{ text: 'ok', kind: 'nonsense' }, { text: '', kind: 'finding' }, 'junk'],
      materials: [{ name: 'Genipin' }],
      methods: 'not a list',
      parameters: [{ entity: 'x', value: 1 }, { entity: 'strain', value: '12', unit: '%', context: '12% strain' }],
    }));
    expect(rows.claims).toEqual([{ text: 'ok', kind: 'finding' }]);
    expect(rows.materials).toEqual([{ name: 'Genipin', role: 'unspecified', amount: undefined }]);
    expect(rows.methods).toEqual([]);
    expect(rows.parameters).toEqual([{ entity: 'strain', value: '12', unit: '%', context: '12% strain' }]);
    expect(() => parseRows('not json')).toThrow(/JSON/);
  });

  it('cuts a long section into pieces the model can hold, at sentences', () => {
    const text = Array.from({ length: 400 }, (_, i) => `Sentence ${i} has exactly six words.`).join(' ');
    const pieces = piecesOf({ heading: 'Results', kind: 'results', text }, 500);
    expect(pieces.length).toBeGreaterThan(3);
    for (const p of pieces) {
      expect(p.text.split(/\s+/).length).toBeLessThanOrEqual(500);
      expect(p.text.endsWith('.')).toBe(true);
    }
    expect(piecesOf({ heading: 'References', kind: 'references', text }, 500)).toEqual([]);
  });
});

describe('the model stage on a library', () => {
  let root: string;
  let lib: Library;
  const now = '2026-09-02T10:00';

  beforeAll(async () => {
    root = mkdtempSync(join(tmpdir(), 'lit-ollama-'));
    lib = createLibrary(root, { name: 'GPU project', extract: 'ollama', embedding: 'ollama', ollama: { chat: 'qwen3:14b' }, now });
    const db = openDb(lib.dbPath);
    upsertPaper(db, { doi: '10.3390/mi15070851', pmcid: 'PMC11278924', title: 'Untitled (staged)', source: 'europepmc' }, now);
    db.close();
    await fetchCandidates(lib, { fetcher: async (url) => ({ ok: url.includes('fullTextXML'), status: 200, text: async () => xml }), now });
    await ingestLibrary(lib, hashEmbedder(), { now });
  });
  afterAll(() => rmSync(root, { recursive: true, force: true }));

  it('remembers its backend in the manifest', () => {
    const reopened = openLibrary(root, 'gpu-project')!;
    expect(reopened.manifest).toMatchObject({ extract: 'ollama', embedding: 'ollama', model: 'ollama:nomic-embed-text' });
    expect(reopened.manifest.ollama).toEqual({ url: 'http://127.0.0.1:11434', chat: 'qwen3:14b', embed: 'nomic-embed-text' });
  });

  it('reads every section into rows once, stamps the paper, and is resumable', async () => {
    const calls: { url: string; body: unknown }[] = [];
    const extractor = ollamaExtractor(lib.manifest.ollama, fakeOllama(calls));
    const report = await extractLibrary(lib, extractor, { now });
    expect(report.extracted).toEqual(['doi:10.3390/mi15070851']);
    expect(report.failed).toEqual([]);
    expect(report.sections).toBeGreaterThan(10);
    expect(calls.length).toBe(report.sections);

    const db = openDb(lib.dbPath);
    try {
      const status = statusView(db, 'ollama:nomic-embed-text');
      expect(status.extracted).toBe(1);
      expect(status.claims).toBe(report.sections);
      expect(status.materials).toBe(report.sections);
      // The miner's parameters stay beside the model's, told apart by source.
      const bySource = runSql(lib, 'select source, count(*) as n from parameters group by source order by source');
      expect(bySource.rows.map((r) => r['source'])).toEqual(['miner', 'ollama:qwen3:14b']);
    } finally {
      db.close();
    }

    const again = await extractLibrary(lib, extractor, { now });
    expect(again.extracted).toEqual([]);
    expect(calls.length).toBe(report.sections);
  });

  it('is a SELECT away: which crosslinkers, at what concentration', () => {
    const result = runSql(lib, "select name, amount, count(*) as n from materials where role = 'crosslinker' group by name, amount");
    expect(result.rows[0]).toMatchObject({ name: 'EDC', amount: '10 mM' });
    const entities = runSql(lib, "select distinct entity from parameters where source != 'miner'");
    expect(entities.rows).toEqual([{ entity: 'EDC concentration' }]);
  });

  it('names a paper the model could not read and carries on', async () => {
    const db = openDb(lib.dbPath);
    upsertPaper(db, { doi: '10.1/second', pmcid: 'PMC2', title: 'Second paper', source: 'europepmc' }, now);
    db.close();
    await fetchCandidates(lib, { fetcher: async () => ({ ok: true, status: 200, text: async () => xml }), now });
    await ingestLibrary(lib, hashEmbedder(), { now });
    const broken = ollamaExtractor(lib.manifest.ollama, async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ message: { content: 'garbage' } }) }));
    const report = await extractLibrary(lib, broken, { now });
    expect(report.extracted).toEqual([]);
    expect(report.failed).toEqual([{ key: 'doi:10.1/second', error: 'The model did not answer with JSON.' }]);
  });
});

describe('a streamed chat answer', () => {
  it('concatenates message.content across NDJSON lines, and still reads a single object whole', async () => {
    const rows = { claims: [], materials: [{ name: 'Genipin', role: 'crosslinker' }], methods: [], parameters: [] };
    const pieces = JSON.stringify(rows).match(/.{1,9}/gs)!;
    const ndjson = [
      ...pieces.map((p) => JSON.stringify({ message: { role: 'assistant', content: p }, done: false })),
      JSON.stringify({ message: { role: 'assistant', content: '' }, done: true }),
    ].join('\n');
    const streaming: FetchLike = async (_url, init) => {
      expect((JSON.parse(init!.body!) as { stream: boolean }).stream).toBe(true);
      return { ok: true, status: 200, text: async () => ndjson };
    };
    const got = await ollamaExtractor(settings, streaming).extract({ heading: 'Methods', text: 'x' }, { title: 'T' });
    expect(got.materials).toEqual([{ name: 'Genipin', role: 'crosslinker', amount: undefined }]);
  });
});
