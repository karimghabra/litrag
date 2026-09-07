/**
 * The library survives the machine (#18): pushed to a bare local remote,
 * cloned elsewhere, the store rebuilt, and the durable rows come home.
 * Real git, temp directories, no network.
 */

import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { attachNote, openDb, replaceModelRows, replaceProfile, upsertPaper, replaceExtraction } from '../src/db.ts';
import { createLibrary, openLibrary } from '../src/library.ts';
import { exportDurables, restoreDurables, syncPush, writeSyncSettings } from '../src/sync.ts';

const now = '2026-09-07T09:00';
let root: string;
let remote: string;
let clone: string;
const key = 'doi:10.1/synced';

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'lit-sync-root-'));
  remote = mkdtempSync(join(tmpdir(), 'lit-sync-remote-'));
  clone = mkdtempSync(join(tmpdir(), 'lit-sync-clone-'));
  execFileSync('git', ['init', '--bare', remote]);

  const lib = createLibrary(root, { name: 'Synced Shelf', now });
  const db = openDb(lib.dbPath);
  upsertPaper(db, { doi: '10.1/synced', title: 'A paper worth keeping', source: 'europepmc' }, now);
  replaceExtraction(
    db,
    key,
    {
      sections: [{ heading: 'Methods', kind: 'methods', text: 'Crosslinked in genipin overnight at four degrees.' }],
      chunks: [{ sectionOrdinal: 0, ordinal: 0, words: 7, text: 'Crosslinked in genipin overnight at four degrees.' }],
      parameters: [],
      references: [],
    },
    now,
  );
  const chunk = (db.prepare('SELECT id FROM chunks WHERE paper = ?').get(key) as { id: number }).id;
  attachNote(db, key, chunk, 'Matches our bench protocol.', now);
  replaceProfile(db, key, 'ollama:test', [{ facet: 'crosslinking', value: 'genipin overnight', evidence: 'Crosslinked in genipin overnight at four degrees.' }]);
  const section = (db.prepare('SELECT id FROM sections WHERE paper = ?').get(key) as { id: number }).id;
  replaceModelRows(db, key, 'ollama:test', [{ section, rows: { claims: [{ text: 'Genipin holds the threads.', kind: 'finding' }], materials: [{ name: 'Genipin', role: 'crosslinker' }], methods: [], parameters: [{ entity: 'crosslinking time', value: 'overnight', unit: 'h', context: 'Crosslinked in genipin overnight.' }] } }], now);
  db.close();
});
afterAll(() => {
  for (const dir of [root, remote, clone]) rmSync(dir, { recursive: true, force: true });
});

describe('lit sync', () => {
  it('exports the durable rows and pushes everything but the store', async () => {
    writeSyncSettings(root, { repo: remote });
    const report = await syncPush(root);
    expect(report.pushed).toBe(true);
    expect(report.committed).toBe(true);
    expect(report.notes).toBe(1);
    expect(report.profiles).toBe(1);
    // A second sync with nothing new is honest about it.
    const again = await syncPush(root);
    expect(again.committed).toBe(false);

    // A fresh bare's HEAD says master; GitHub sets HEAD server-side, a local
    // bare does not — name the branch and the difference disappears.
    execFileSync('git', ['clone', '-b', 'main', remote, join(clone, 'library')]);
    const cloned = join(clone, 'library');
    expect(existsSync(join(cloned, 'synced-shelf', 'library.json'))).toBe(true);
    expect(existsSync(join(cloned, 'synced-shelf', 'notes.jsonl'))).toBe(true);
    expect(existsSync(join(cloned, 'synced-shelf', 'profiles.jsonl'))).toBe(true);
    expect(existsSync(join(cloned, 'synced-shelf', 'extract.jsonl'))).toBe(true);
    expect(existsSync(join(cloned, 'synced-shelf', 'text.jsonl'))).toBe(true);
    // The store never travels.
    expect(existsSync(join(cloned, 'synced-shelf', 'lit.sqlite'))).toBe(false);
    expect(readFileSync(join(cloned, '.gitignore'), 'utf8')).toContain('lit.sqlite');
  });

  it('restores the whole reading — text, extract rows, profiles, notes — without touching a source', () => {
    const cloned = join(clone, 'library');
    const lib = openLibrary(cloned, 'synced-shelf')!;
    // Only the staging is simulated: the paper's row exists, nothing else.
    const db = openDb(lib.dbPath);
    upsertPaper(db, { doi: '10.1/synced', title: 'A paper worth keeping', source: 'europepmc' }, now);
    db.close();
    const first = restoreDurables(lib);
    expect(first.texts).toBe(1);
    expect(first.extracted).toBe(1);
    expect(first.notes).toBe(1);
    expect(first.profiles).toBe(1);
    const second = restoreDurables(lib);
    expect(second).toEqual({ notes: 0, profiles: 0, extracted: 0, texts: 0, skipped: 0 });
    const check = openDb(lib.dbPath, { readOnly: true });
    try {
      expect((check.prepare('SELECT COUNT(*) n FROM chunks WHERE paper = ?').get(key) as { n: number }).n).toBe(1);
      expect((check.prepare('SELECT status FROM papers WHERE key = ?').get(key) as { status: string }).status).toBe('ingested');
      const note = check.prepare('SELECT text FROM notes').get() as { text: string };
      expect(note.text).toBe('Matches our bench protocol.');
      const profile = check.prepare('SELECT value FROM profiles').get() as { value: string };
      expect(profile.value).toBe('genipin overnight');
      const claim = check.prepare('SELECT text FROM claims WHERE paper = ?').get(key) as { text: string };
      expect(claim.text).toBe('Genipin holds the threads.');
      const stamped = check.prepare('SELECT profiled_with p, extracted_with e FROM papers WHERE key = ?').get(key) as { p: string; e: string };
      expect(stamped.p).toBe('ollama:test');
      expect(stamped.e).toBe('ollama:test');
    } finally {
      check.close();
    }
  });

  it('refuses to sync with no remote configured, with guidance', async () => {
    const bare = mkdtempSync(join(tmpdir(), 'lit-sync-none-'));
    try {
      await expect(syncPush(bare)).rejects.toThrow(/lit sync --repo/);
    } finally {
      rmSync(bare, { recursive: true, force: true });
    }
  });

  it('exportDurables answers zero counts for an empty library', () => {
    const lib = createLibrary(root, { name: 'Empty Sync', now });
    openDb(lib.dbPath).close();
    expect(exportDurables(lib)).toEqual({ notes: 0, profiles: 0, extracted: 0, texts: 0 });
  });
});
