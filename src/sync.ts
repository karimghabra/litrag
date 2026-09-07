/**
 * The library survives the machine (#18).
 *
 * Git, of the whole library root, to a private remote the user configures —
 * the deliberate exception to "paper text goes nowhere": the point of a
 * backup is that the text exists somewhere else. What is synced is what
 * cannot be recomputed: the papers themselves, the manifests, and — exported
 * as JSON lines beside each library before every push — the notes (the
 * user's voice) and the profiles (a GPU-day of answers). The store itself
 * (lit.sqlite, the model cache) stays out of git: chunks, vectors, entities
 * and the model stage rebuild from the papers, and a churning binary would
 * bloat every push. After a clone and a re-ingest, `lit restore` re-adopts
 * the exported rows, idempotently; notes re-anchor by their quotes as they
 * already do.
 */

import { execFile } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { notesOf, openDb, paperByKey, profileOf, type NoteRow, type ProfileRow } from './db.ts';
import { listLibraries, type Library } from './library.ts';

const run = promisify(execFile);

export interface SyncSettings {
  repo?: string;
}

export function syncSettingsFile(root: string): string {
  return join(root, 'sync.json');
}

export function readSyncSettings(root: string): SyncSettings {
  try {
    return JSON.parse(readFileSync(syncSettingsFile(root), 'utf8')) as SyncSettings;
  } catch {
    return {};
  }
}

export function writeSyncSettings(root: string, settings: SyncSettings): void {
  writeFileSync(syncSettingsFile(root), `${JSON.stringify(settings, null, 2)}\n`, 'utf8');
}

async function git(root: string, ...args: string[]): Promise<string> {
  try {
    const { stdout } = await run('git', args, { cwd: root, maxBuffer: 16 * 1024 * 1024 });
    return String(stdout);
  } catch (error) {
    const e = error as { stderr?: string; message?: string };
    throw new Error(`git ${args[0]}: ${(e.stderr || e.message || '').toString().trim().slice(0, 300)}`);
  }
}

const IGNORE = 'lit.sqlite\nlit.sqlite-wal\nlit.sqlite-shm\nmodels/\n';

/** The rows that cannot be recomputed, written beside the library as text. */
export function exportDurables(lib: Library): { notes: number; profiles: number } {
  const db = openDb(lib.dbPath, { readOnly: true });
  try {
    const papers = (db.prepare('SELECT key FROM papers').all() as { key: string }[]).map((p) => p.key);
    const notes: NoteRow[] = [];
    const profiles: (ProfileRow & { paper: string; source: string })[] = [];
    for (const key of papers) {
      notes.push(...notesOf(db, key));
      let source = '';
      try {
        source = ((db.prepare('SELECT profiled_with s FROM papers WHERE key = ?').get(key) as { s: string | null } | undefined)?.s ?? '');
      } catch {
        source = '';
      }
      for (const row of profileOf(db, key)) profiles.push({ ...row, paper: key, source });
    }
    writeFileSync(join(lib.dir, 'notes.jsonl'), notes.map((n) => JSON.stringify(n)).join('\n') + (notes.length ? '\n' : ''), 'utf8');
    writeFileSync(join(lib.dir, 'profiles.jsonl'), profiles.map((p) => JSON.stringify(p)).join('\n') + (profiles.length ? '\n' : ''), 'utf8');
    return { notes: notes.length, profiles: profiles.length };
  } finally {
    db.close();
  }
}

export interface SyncReport {
  committed: boolean;
  pushed: boolean;
  notes: number;
  profiles: number;
  message: string;
}

export async function syncPush(root: string, log: (line: string) => void = () => {}): Promise<SyncReport> {
  const settings = readSyncSettings(root);
  if (!settings.repo) {
    throw new Error('No sync remote yet. `lit sync --repo <private git url>` sets it once; every later `lit sync` pushes.');
  }
  if (!existsSync(join(root, '.git'))) {
    await git(root, 'init');
    await git(root, 'symbolic-ref', 'HEAD', 'refs/heads/main');
  }
  // The remote follows the settings file, which is itself synced.
  const remotes = await git(root, 'remote');
  if (remotes.split('\n').map((r) => r.trim()).includes('origin')) await git(root, 'remote', 'set-url', 'origin', settings.repo);
  else await git(root, 'remote', 'add', 'origin', settings.repo);
  writeFileSync(join(root, '.gitignore'), IGNORE, 'utf8');

  let notes = 0;
  let profiles = 0;
  for (const lib of listLibraries(root)) {
    const counts = exportDurables(lib);
    notes += counts.notes;
    profiles += counts.profiles;
    log(`exported ${lib.manifest.id}: ${counts.notes} notes, ${counts.profiles} profile rows`);
  }

  await git(root, 'add', '-A');
  const dirty = (await git(root, 'status', '--porcelain')).trim().length > 0;
  let committed = false;
  if (dirty) {
    try {
      await git(root, 'commit', '-m', `lit sync ${new Date().toISOString().slice(0, 16)}`);
    } catch (error) {
      // A machine with no git identity gets a local one and tries again.
      if (!String(error).includes('user.name') && !String(error).includes('identity')) throw error;
      await git(root, 'config', 'user.name', 'litrag sync');
      await git(root, 'config', 'user.email', 'litrag@local');
      await git(root, 'commit', '-m', `lit sync ${new Date().toISOString().slice(0, 16)}`);
    }
    committed = true;
    log('committed');
  }
  // Two machines, one library: take theirs first, then send ours.
  try {
    await git(root, 'pull', '--rebase', 'origin', 'main');
  } catch (error) {
    // The very first push has nothing to pull; anything else is real.
    if (!String(error).includes("couldn't find remote ref") && !String(error).includes('does not appear')) throw error;
  }
  await git(root, 'push', '-u', 'origin', 'main');
  log('pushed');
  return {
    committed,
    pushed: true,
    notes,
    profiles,
    message: committed ? `Synced: papers, manifests, ${notes} notes, ${profiles} profile rows.` : 'Already clean; pushed what was waiting.',
  };
}

/**
 * After a clone and a re-ingest, the exported rows come home (#18).
 * Idempotent: a note already present (paper, quote, text) is not doubled,
 * and a paper that already has profile rows keeps them.
 */
export function restoreDurables(lib: Library): { notes: number; profiles: number; skipped: number } {
  const db = openDb(lib.dbPath);
  const report = { notes: 0, profiles: 0, skipped: 0 };
  try {
    const lines = (file: string): Record<string, unknown>[] => {
      const path = join(lib.dir, file);
      if (!existsSync(path)) return [];
      return readFileSync(path, 'utf8')
        .split('\n')
        .filter((l) => l.trim())
        .map((l) => JSON.parse(l) as Record<string, unknown>);
    };
    for (const row of lines('notes.jsonl')) {
      const paper = String(row['paper'] ?? '');
      if (!paperByKey(db, paper)) {
        report.skipped += 1;
        continue;
      }
      const exists = db
        .prepare('SELECT 1 FROM notes WHERE paper = ? AND quote = ? AND text = ?')
        .get(paper, String(row['quote'] ?? ''), String(row['text'] ?? ''));
      if (exists) continue;
      db.prepare('INSERT INTO notes (paper, chunk, quote, text, created_at) VALUES (?, ?, ?, ?, ?)').run(
        paper,
        row['chunk'] === null || row['chunk'] === undefined ? null : Number(row['chunk']),
        String(row['quote'] ?? ''),
        String(row['text'] ?? ''),
        String(row['createdAt'] ?? new Date().toISOString().slice(0, 16)),
      );
      report.notes += 1;
    }
    const byPaper = new Map<string, Record<string, unknown>[]>();
    for (const row of lines('profiles.jsonl')) {
      const paper = String(row['paper'] ?? '');
      const list = byPaper.get(paper) ?? [];
      list.push(row);
      byPaper.set(paper, list);
    }
    for (const [paper, rows] of byPaper) {
      if (!paperByKey(db, paper)) {
        report.skipped += rows.length;
        continue;
      }
      const has = (db.prepare('SELECT COUNT(*) n FROM profiles WHERE paper = ?').get(paper) as { n: number }).n;
      if (has > 0) continue;
      const insert = db.prepare('INSERT INTO profiles (paper, facet, value, evidence, source) VALUES (?, ?, ?, ?, ?)');
      for (const row of rows) {
        insert.run(paper, String(row['facet'] ?? ''), String(row['value'] ?? ''), String(row['evidence'] ?? ''), String(row['source'] ?? 'restored'));
        report.profiles += 1;
      }
      const source = String(rows[0]!['source'] ?? 'restored');
      db.prepare('UPDATE papers SET profiled_with = ? WHERE key = ?').run(source, paper);
    }
    return report;
  } finally {
    db.close();
  }
}
