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
import { notesOf, openDb, paperByKey, profileOf, replaceExtraction, replaceModelRows, type Extraction, type ModelRows, type NoteRow, type ProfileRow } from './db.ts';
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

/**
 * The rows that cannot be cheaply recomputed, written beside the library as
 * text: notes (the user's voice), profiles, and the extract stage's rows —
 * a GPU-day of claims, materials, methods and model parameters, keyed by
 * section ORDINAL, which a re-ingest of the same file reproduces exactly.
 */
export function exportDurables(lib: Library): { notes: number; profiles: number; extracted: number; texts: number } {
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
    const extractLines: string[] = [];
    try {
      const extracted = db
        .prepare('SELECT key, extracted_with FROM papers WHERE extracted_with IS NOT NULL')
        .all() as { key: string; extracted_with: string }[];
      for (const paper of extracted) {
        const bySection = new Map<number, ModelRows>();
        const rowsOf = (ordinal: number): ModelRows => {
          let rows = bySection.get(ordinal);
          if (!rows) {
            rows = { claims: [], materials: [], methods: [], parameters: [] };
            bySection.set(ordinal, rows);
          }
          return rows;
        };
        for (const c of db.prepare('SELECT c.text, c.kind, s.ordinal FROM claims c JOIN sections s ON s.id = c.section WHERE c.paper = ?').all(paper.key) as { text: string; kind: string; ordinal: number }[]) {
          rowsOf(c.ordinal).claims.push({ text: c.text, kind: c.kind });
        }
        for (const m of db.prepare('SELECT m.name, m.role, m.amount, s.ordinal FROM materials m JOIN sections s ON s.id = m.section WHERE m.paper = ?').all(paper.key) as { name: string; role: string; amount: string | null; ordinal: number }[]) {
          rowsOf(m.ordinal).materials.push({ name: m.name, role: m.role, ...(m.amount ? { amount: m.amount } : {}) });
        }
        for (const m of db.prepare('SELECT m.name, m.description, s.ordinal FROM methods m JOIN sections s ON s.id = m.section WHERE m.paper = ?').all(paper.key) as { name: string; description: string; ordinal: number }[]) {
          rowsOf(m.ordinal).methods.push({ name: m.name, description: m.description });
        }
        for (const p of db.prepare("SELECT p.entity, p.value, p.unit, p.sentence, s.ordinal FROM parameters p JOIN sections s ON s.id = p.section WHERE p.paper = ? AND p.source != 'miner'").all(paper.key) as { entity: string | null; value: string; unit: string; sentence: string; ordinal: number }[]) {
          rowsOf(p.ordinal).parameters.push({ entity: p.entity ?? '', value: p.value, unit: p.unit, context: p.sentence });
        }
        extractLines.push(JSON.stringify({ paper: paper.key, model: paper.extracted_with, sections: [...bySection.entries()].map(([ordinal, rows]) => ({ ordinal, rows })) }));
      }
    } catch {
      // A store from before the model stage has nothing to export.
    }
    // The ingested text itself (#18 follow-up): the paper as the reader read
    // it — sections, chunks, the miner's parameters, the references — so a
    // restore rebuilds the store without touching a single PDF. The sources
    // still travel too: the reader improves, and --reread re-reads them.
    const textLines: string[] = [];
    for (const paper of db.prepare("SELECT key FROM papers WHERE status = 'ingested'").all() as { key: string }[]) {
      const sections = db
        .prepare('SELECT ordinal, heading, kind, page, text FROM sections WHERE paper = ? ORDER BY ordinal')
        .all(paper.key) as { ordinal: number; heading: string; kind: string; page: number | null; text: string }[];
      if (!sections.length) continue;
      const chunks = db
        .prepare('SELECT s.ordinal sectionOrdinal, c.ordinal, c.page, c.words, c.text FROM chunks c JOIN sections s ON s.id = c.section WHERE c.paper = ? ORDER BY s.ordinal, c.ordinal')
        .all(paper.key) as { sectionOrdinal: number; ordinal: number; page: number | null; words: number; text: string }[];
      const parameters = db
        .prepare(
          `SELECT s.ordinal sectionOrdinal, c.ordinal chunkOrdinal, p.value, p.value_num valueNum, p.unit, p.kind, p.sentence
             FROM parameters p JOIN sections s ON s.id = p.section LEFT JOIN chunks c ON c.id = p.chunk
            WHERE p.paper = ? AND p.source = 'miner'`,
        )
        .all(paper.key) as { sectionOrdinal: number; chunkOrdinal: number | null; value: string; valueNum: number | null; unit: string; kind: string; sentence: string }[];
      const references = db
        .prepare('SELECT title, doi, pmid FROM refs WHERE paper = ? ORDER BY ordinal')
        .all(paper.key) as { title: string; doi: string | null; pmid: string | null }[];
      textLines.push(
        JSON.stringify({
          paper: paper.key,
          extraction: {
            sections: sections.map((s) => ({ heading: s.heading, kind: s.kind, text: s.text, ...(s.page !== null ? { page: s.page } : {}) })),
            chunks: chunks.map((c) => ({ sectionOrdinal: c.sectionOrdinal, ordinal: c.ordinal, words: c.words, text: c.text, ...(c.page !== null ? { page: c.page } : {}) })),
            parameters: parameters.map((p) => ({ sectionOrdinal: p.sectionOrdinal, ...(p.chunkOrdinal !== null ? { chunkOrdinal: p.chunkOrdinal } : {}), value: p.value, valueNum: p.valueNum, unit: p.unit, kind: p.kind, sentence: p.sentence })),
            references: references.map((r) => ({ title: r.title, ...(r.doi ? { doi: r.doi } : {}), ...(r.pmid ? { pmid: r.pmid } : {}) })),
          },
        }),
      );
    }
    writeFileSync(join(lib.dir, 'notes.jsonl'), notes.map((n) => JSON.stringify(n)).join('\n') + (notes.length ? '\n' : ''), 'utf8');
    writeFileSync(join(lib.dir, 'profiles.jsonl'), profiles.map((p) => JSON.stringify(p)).join('\n') + (profiles.length ? '\n' : ''), 'utf8');
    writeFileSync(join(lib.dir, 'extract.jsonl'), extractLines.join('\n') + (extractLines.length ? '\n' : ''), 'utf8');
    writeFileSync(join(lib.dir, 'text.jsonl'), textLines.join('\n') + (textLines.length ? '\n' : ''), 'utf8');
    return { notes: notes.length, profiles: profiles.length, extracted: extractLines.length, texts: textLines.length };
  } finally {
    db.close();
  }
}

export interface SyncReport {
  committed: boolean;
  pushed: boolean;
  notes: number;
  profiles: number;
  extracted: number;
  texts: number;
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
  let extracted = 0;
  let texts = 0;
  for (const lib of listLibraries(root)) {
    const counts = exportDurables(lib);
    notes += counts.notes;
    profiles += counts.profiles;
    extracted += counts.extracted;
    texts += counts.texts;
    log(`exported ${lib.manifest.id}: ${counts.texts} ingested texts, ${counts.extracted} extracted papers, ${counts.profiles} profile rows, ${counts.notes} notes`);
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
    extracted,
    texts,
    message: committed
      ? `Synced: sources, manifests, the ingested text of ${texts} papers, extract rows of ${extracted}, ${profiles} profile rows, ${notes} notes.`
      : 'Already clean; pushed what was waiting.',
  };
}

/**
 * After a clone and a re-ingest, the exported rows come home (#18).
 * Idempotent: a note already present (paper, quote, text) is not doubled,
 * and a paper that already has profile rows keeps them.
 */
export function restoreDurables(lib: Library): { notes: number; profiles: number; extracted: number; texts: number; skipped: number } {
  const db = openDb(lib.dbPath);
  const report = { notes: 0, profiles: 0, extracted: 0, texts: 0, skipped: 0 };
  try {
    const lines = (file: string): Record<string, unknown>[] => {
      const path = join(lib.dir, file);
      if (!existsSync(path)) return [];
      return readFileSync(path, 'utf8')
        .split('\n')
        .filter((l) => l.trim())
        .map((l) => JSON.parse(l) as Record<string, unknown>);
    };
    // The text first (the notes and the model rows anchor onto it): the
    // paper as the reader read it, rebuilt without touching a single PDF.
    // The next `lit ingest` embeds the restored chunks.
    for (const row of lines('text.jsonl')) {
      const paper = String(row['paper'] ?? '');
      if (!paperByKey(db, paper)) {
        report.skipped += 1;
        continue;
      }
      const has = (db.prepare('SELECT COUNT(*) n FROM chunks WHERE paper = ?').get(paper) as { n: number }).n;
      if (has > 0) continue;
      replaceExtraction(db, paper, row['extraction'] as unknown as Extraction, new Date().toISOString().slice(0, 16));
      db.prepare("UPDATE papers SET status = 'ingested' WHERE key = ?").run(paper);
      report.texts += 1;
    }
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
    // The extract stage's rows come home too (#18) — the GPU already earned
    // them once. Section ordinals map onto the re-ingested sections; a paper
    // already extracted keeps what it has.
    for (const row of lines('extract.jsonl')) {
      const paper = String(row['paper'] ?? '');
      const existing = paperByKey(db, paper);
      if (!existing) {
        report.skipped += 1;
        continue;
      }
      if (existing.extracted_with) continue;
      const sections = db.prepare('SELECT id, ordinal FROM sections WHERE paper = ?').all(paper) as { id: number; ordinal: number }[];
      if (!sections.length) {
        report.skipped += 1;
        continue;
      }
      const byOrdinal = new Map(sections.map((s) => [s.ordinal, s.id]));
      const mapped: { section: number; rows: ModelRows }[] = [];
      for (const entry of (row['sections'] as { ordinal: number; rows: ModelRows }[]) ?? []) {
        const id = byOrdinal.get(Number(entry.ordinal));
        if (id !== undefined) mapped.push({ section: id, rows: entry.rows });
      }
      if (!mapped.length) {
        report.skipped += 1;
        continue;
      }
      replaceModelRows(db, paper, String(row['model'] ?? 'restored'), mapped, new Date().toISOString().slice(0, 16));
      report.extracted += 1;
    }
    return report;
  } finally {
    db.close();
  }
}
