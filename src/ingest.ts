/**
 * The pipeline: candidates fetched, papers read, rows written, chunks embedded.
 *
 * Idempotent by construction — a paper is keyed by DOI, then PMID, then the
 * hash of its file; a chunk is embedded once per model; running it again
 * changes nothing. Every step is a status a person can read with `lit status`.
 */

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs';
import { basename, extname, join } from 'node:path';
import type { DatabaseSync } from 'node:sqlite';
import { chunkSections } from './chunk.ts';
import {
  chunksWithoutVectors,
  insertVector,
  openDb,
  paperByKey,
  papersByStatus,
  papersToExtract,
  replaceExtraction,
  replaceModelRows,
  sectionsOf,
  setPaperFile,
  setPaperStatus,
  upsertPaper,
  type Extraction,
  type ModelRows,
  type PaperRow,
} from './db.ts';
import { splitSentences } from './chunk.ts';
import type { Extractor } from './ollama.ts';
import type { Embedder } from './embed.ts';
import { parseJats } from './jats.ts';
import type { Library } from './library.ts';
import { mineSections } from './parameters.ts';
import { findDoi, pdfPages } from './pdf.ts';
import { sectionsFromPages } from './sections.ts';
import { annotationsFor, fullTextXml, type Fetcher, defaultFetcher } from './sources/europepmc.ts';
import { entityId } from './db.ts';

export type Log = (line: string) => void;

const quiet: Log = () => {};

export function fileNameFor(key: string, ext: string): string {
  return `${key.replace(/[^a-zA-Z0-9._-]+/g, '_')}${ext}`;
}

function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}

export interface FetchReport {
  fetched: string[];
  needsPdf: string[];
}

/** Full text for every candidate that has any to give; the rest wait for a PDF. */
export async function fetchCandidates(lib: Library, options: { fetcher?: Fetcher; log?: Log; now?: string } = {}): Promise<FetchReport> {
  const fetcher = options.fetcher ?? defaultFetcher;
  const log = options.log ?? quiet;
  const db = openDb(lib.dbPath);
  const report: FetchReport = { fetched: [], needsPdf: [] };
  try {
    for (const paper of papersByStatus(db, 'candidate')) {
      if (paper.pmcid) {
        const xml = await fullTextXml(paper.pmcid, fetcher);
        if (xml) {
          const file = fileNameFor(paper.key, '.xml');
          mkdirSync(lib.papersDir, { recursive: true });
          writeFileSync(join(lib.papersDir, file), xml);
          setPaperFile(db, paper.key, file, sha256(Buffer.from(xml)), 'fetched');
          report.fetched.push(paper.key);
          log(`fetched  ${paper.key}  ${paper.title}`);
          continue;
        }
      }
      setPaperStatus(db, paper.key, 'needs-pdf');
      report.needsPdf.push(paper.key);
      log(`no text  ${paper.key}  ${paper.title}`);
    }
  } finally {
    db.close();
  }
  return report;
}

export interface IngestReport {
  inbox: string[];
  ingested: string[];
  failed: { key: string; error: string }[];
  embedded: number;
}

async function extractionFor(lib: Library, paper: PaperRow): Promise<Extraction> {
  if (!paper.file) throw new Error('no file');
  const path = join(lib.papersDir, paper.file);
  const bytes = readFileSync(path);
  if (extname(paper.file).toLowerCase() === '.xml') {
    const article = parseJats(bytes.toString('utf8'));
    return {
      sections: article.sections,
      chunks: chunkSections(article.sections),
      parameters: mineSections(article.sections),
      references: article.references,
      meta: {
        doi: article.doi,
        pmid: article.pmid,
        pmcid: article.pmcid,
        title: article.title,
        year: article.year,
        journal: article.journal,
        authors: article.authors,
        abstract: article.abstract,
      },
    };
  }
  const pages = await pdfPages(new Uint8Array(bytes));
  const sections = sectionsFromPages(pages);
  const front = sections[0];
  return {
    sections,
    chunks: chunkSections(sections),
    parameters: mineSections(sections),
    references: [],
    meta: {
      doi: findDoi(pages.slice(0, 2).join('\n')),
      title: front ? titleGuess(front.text) : undefined,
    },
  };
}

/** The first line of a PDF that reads like a title: long enough, no digits-only. */
function titleGuess(front: string): string | undefined {
  for (const line of front.split(/\n+/)) {
    const t = line.trim();
    if (t.length >= 20 && t.length <= 200 && !/^\d/.test(t) && !/doi|copyright|©|received|accepted|journal/i.test(t)) return t;
  }
  return undefined;
}

/** The papers still waiting for a file, keyed by the name `collect` would give their PDF. */
function unfiledByName(db: DatabaseSync): Map<string, string> {
  const unfiled = db.prepare('SELECT key FROM papers WHERE file IS NULL').all() as { key: string }[];
  return new Map(unfiled.map((p) => [fileNameFor(p.key, '.pdf'), p.key]));
}

/**
 * PDFs a person dropped in the inbox become papers in the store. A file named
 * the way `collect` names them is the paper it was caught for — the window
 * knew the DOI, and the name carries it, so the pages need not. Anything else
 * is filed by DOI when its pages name one, else by its bytes.
 */
export function takeInbox(lib: Library, db: DatabaseSync, now: string, log: Log): string[] {
  if (!existsSync(lib.inboxDir)) return [];
  const taken: string[] = [];
  const owners = unfiledByName(db);
  for (const entry of readdirSync(lib.inboxDir)) {
    if (extname(entry).toLowerCase() !== '.pdf') continue;
    const from = join(lib.inboxDir, entry);
    const bytes = readFileSync(from);
    const hash = sha256(bytes);
    const key = owners.get(entry) ?? upsertPaper(db, { title: `Untitled (${basename(entry)})`, source: 'inbox' }, now, hash).key;
    const file = fileNameFor(key, '.pdf');
    mkdirSync(lib.papersDir, { recursive: true });
    renameSync(from, join(lib.papersDir, file));
    setPaperFile(db, key, file, hash, 'fetched');
    taken.push(key);
    log(`inbox    ${key}  ${entry}`);
  }
  return taken;
}

/**
 * A stray filed by its bytes, because its pages name no DOI, is reunited with
 * the paper its file name points to. Earlier ingests made such strays before
 * the name was a hint; the cascade clears the stray's rows, and the owner is
 * read fresh in the same pass. Running it again finds nothing — idempotent.
 */
export function reuniteNamedStrays(db: DatabaseSync, log: Log): string[] {
  const strays = db.prepare("SELECT key, title, file, sha256 FROM papers WHERE key LIKE 'sha:%' AND file IS NOT NULL").all() as { key: string; title: string; file: string; sha256: string | null }[];
  if (!strays.length) return [];
  const owners = unfiledByName(db);
  const reunited: string[] = [];
  for (const s of strays) {
    const named = /^Untitled \((.+)\)$/.exec(s.title);
    const owner = named ? owners.get(named[1]!) : undefined;
    if (!owner) continue;
    db.prepare("UPDATE papers SET file = ?, sha256 = ?, status = 'fetched' WHERE key = ?").run(s.file, s.sha256, owner);
    db.prepare('DELETE FROM papers WHERE key = ?').run(s.key);
    owners.delete(named![1]!);
    reunited.push(owner);
    log(`reunited ${owner}  (was ${s.key})`);
  }
  return reunited;
}

/**
 * Read every fetched paper into rows, then embed every chunk the model has
 * not seen. A paper that fails to read stays `fetched` with the error named,
 * so one bad PDF never stops the rest.
 */
export async function ingestLibrary(lib: Library, embedder: Embedder, options: { log?: Log; now?: string; reread?: boolean } = {}): Promise<IngestReport> {
  const log = options.log ?? quiet;
  const now = options.now ?? new Date().toISOString().slice(0, 16);
  const db = openDb(lib.dbPath);
  const report: IngestReport = { inbox: [], ingested: [], failed: [], embedded: 0 };
  try {
    // The reader improved: every paper on disk goes through it again. The
    // chunks are remade, so their vectors are too; the model stage's rows
    // are cleared with them and the stamp with the rows.
    if (options.reread) db.prepare("UPDATE papers SET status = 'fetched' WHERE status = 'ingested' AND file IS NOT NULL").run();
    reuniteNamedStrays(db, log);
    report.inbox = takeInbox(lib, db, now, log);
    for (const paper of papersByStatus(db, 'fetched')) {
      try {
        const extraction = await extractionFor(lib, paper);
        // A PDF whose text names a DOI a candidate already has is that candidate.
        const doi = extraction.meta?.doi?.toLowerCase();
        if (doi && !paper.doi) {
          const twin = db.prepare('SELECT key FROM papers WHERE doi = ? AND key != ?').get(doi, paper.key) as { key: string } | undefined;
          if (twin) {
            db.prepare('UPDATE papers SET file = ?, sha256 = ?, status = ? WHERE key = ?').run(paper.file, paper.sha256, 'fetched', twin.key);
            db.prepare('DELETE FROM papers WHERE key = ?').run(paper.key);
            const merged = paperByKey(db, twin.key)!;
            replaceExtraction(db, merged.key, extraction, now);
            report.ingested.push(merged.key);
            log(`ingested ${merged.key}  (merged from ${paper.key})`);
            continue;
          }
        }
        replaceExtraction(db, paper.key, extraction, now);
        report.ingested.push(paper.key);
        log(`ingested ${paper.key}  ${extraction.sections.length} sections, ${extraction.chunks.length} chunks, ${extraction.parameters.length} parameters`);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        report.failed.push({ key: paper.key, error: message });
        log(`failed   ${paper.key}  ${message}`);
      }
    }
    const pending = chunksWithoutVectors(db, embedder.model);
    for (let i = 0; i < pending.length; i += 64) {
      const batch = pending.slice(i, i + 64);
      const vectors = await embedder.embed(batch.map((c) => c.text));
      db.exec('BEGIN');
      batch.forEach((c, j) => insertVector(db, c.id, embedder.model, vectors[j]!));
      db.exec('COMMIT');
      report.embedded += batch.length;
      log(`embedded ${Math.min(i + 64, pending.length)}/${pending.length}`);
    }
  } finally {
    db.close();
  }
  return report;
}

export interface ExtractReport {
  extracted: string[];
  failed: { key: string; error: string }[];
  sections: number;
}

/** Sections the model reads: the paper's own prose, in pieces it can hold. */
export function piecesOf(section: { heading: string; kind: string; text: string }, maxWords = 1200): { heading: string; text: string }[] {
  if (section.kind === 'references' || section.kind === 'back') return [];
  const count = (t: string) => t.split(/\s+/).filter(Boolean).length;
  if (count(section.text) <= maxWords) return [{ heading: section.heading, text: section.text }];
  const out: { heading: string; text: string }[] = [];
  let current: string[] = [];
  let n = 0;
  for (const sentence of splitSentences(section.text)) {
    const w = count(sentence);
    if (n && n + w > maxWords) {
      out.push({ heading: section.heading, text: current.join(' ') });
      current = [];
      n = 0;
    }
    current.push(sentence);
    n += w;
  }
  if (current.length) out.push({ heading: section.heading, text: current.join(' ') });
  return out;
}

/**
 * The model stage: every ingested paper the model has not read, section by
 * section, into claims, materials, methods and named parameters. Slow on
 * purpose — this is the GPU's job — and resumable: each paper is stamped
 * with the model that read it, so a run cut short picks up where it stopped.
 */
export async function extractLibrary(lib: Library, extractor: Extractor, options: { log?: Log; now?: string; limit?: number } = {}): Promise<ExtractReport> {
  const log = options.log ?? quiet;
  const now = options.now ?? new Date().toISOString().slice(0, 16);
  const db = openDb(lib.dbPath);
  const report: ExtractReport = { extracted: [], failed: [], sections: 0 };
  try {
    const papers = papersToExtract(db, extractor.model).slice(0, options.limit ?? Number.MAX_SAFE_INTEGER);
    for (const paper of papers) {
      try {
        const rows: { section: number; rows: ModelRows }[] = [];
        for (const section of sectionsOf(db, paper.key)) {
          const merged: ModelRows = { claims: [], materials: [], methods: [], parameters: [] };
          for (const piece of piecesOf(section)) {
            const found = await extractor.extract(piece, { title: paper.title });
            merged.claims.push(...found.claims);
            merged.materials.push(...found.materials);
            merged.methods.push(...found.methods);
            merged.parameters.push(...found.parameters);
            report.sections += 1;
          }
          rows.push({ section: section.id, rows: merged });
        }
        replaceModelRows(db, paper.key, extractor.model, rows, now);
        report.extracted.push(paper.key);
        const total = rows.reduce((n, r) => n + r.rows.claims.length + r.rows.materials.length + r.rows.methods.length + r.rows.parameters.length, 0);
        log(`extracted ${paper.key}  ${total} rows from ${rows.length} sections`);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        report.failed.push({ key: paper.key, error: message });
        log(`failed    ${paper.key}  ${message}`);
      }
    }
  } finally {
    db.close();
  }
  return report;
}

export interface AnnotateReport {
  annotated: string[];
  skipped: string[];
  mentions: number;
}

/** Europe PMC's section labels onto the store's section kinds. */
function kindOfAnnotationSection(section: string): string | null {
  const s = section.toLowerCase();
  if (s.startsWith('abstract')) return 'abstract';
  if (s.startsWith('intro')) return 'introduction';
  if (s.startsWith('method')) return 'methods';
  if (s.startsWith('result')) return 'results';
  if (s.startsWith('discussion') || s.startsWith('conclusion')) return 'discussion';
  return null;
}

/**
 * Entity nodes for free: Europe PMC's text-mined terms for every ingested
 * open-access paper the graph has not seen. Each term is pinned to the chunks
 * that contain it, in the section Europe PMC says it came from; a term found
 * nowhere in the text is pinned to the paper.
 */
export async function annotateLibrary(lib: Library, options: { fetcher?: Fetcher; log?: Log; now?: string } = {}): Promise<AnnotateReport> {
  const fetcher = options.fetcher ?? defaultFetcher;
  const log = options.log ?? quiet;
  const now = options.now ?? new Date().toISOString().slice(0, 16);
  const db = openDb(lib.dbPath);
  const report: AnnotateReport = { annotated: [], skipped: [], mentions: 0 };
  try {
    const papers = db.prepare("SELECT key, pmcid FROM papers WHERE status = 'ingested' AND annotated_at IS NULL ORDER BY added_at, key").all() as { key: string; pmcid: string | null }[];
    for (const paper of papers) {
      if (!paper.pmcid) {
        report.skipped.push(paper.key);
        continue;
      }
      const annotations = await annotationsFor(paper.pmcid, fetcher);
      const chunks = db.prepare('SELECT c.id, c.text, s.kind FROM chunks c JOIN sections s ON s.id = c.section WHERE c.paper = ? ORDER BY c.id').all(paper.key) as { id: number; text: string; kind: string }[];
      const seen = new Map<string, { count: number; name: string; kind: string; sections: Set<string> }>();
      for (const a of annotations) {
        const kind = a.type.toLowerCase().replace(/[^a-z]+/g, '-');
        const key = `${kind}:${a.exact.toLowerCase()}`;
        const entry = seen.get(key) ?? { count: 0, name: a.exact, kind, sections: new Set<string>() };
        entry.count += 1;
        const sectionKind = kindOfAnnotationSection(a.section);
        if (sectionKind) entry.sections.add(sectionKind);
        seen.set(key, entry);
      }
      db.exec('BEGIN');
      try {
        db.prepare("DELETE FROM mentions WHERE paper = ? AND source = 'europepmc'").run(paper.key);
        const upsert = db.prepare('INSERT INTO mentions (entity, paper, chunk, count, source) VALUES (?, ?, ?, ?, ?) ON CONFLICT(entity, paper, chunk, source) DO UPDATE SET count = count + excluded.count');
        for (const entry of seen.values()) {
          const id = entityId(db, entry.name, entry.kind);
          const needle = entry.name.toLowerCase();
          let hit = chunks.filter((c) => c.text.toLowerCase().includes(needle));
          if (entry.sections.size) {
            const inSection = hit.filter((c) => entry.sections.has(c.kind));
            if (inSection.length) hit = inSection;
          }
          if (!hit.length) {
            upsert.run(id, paper.key, null, entry.count, 'europepmc');
            report.mentions += 1;
            continue;
          }
          for (const c of hit) {
            upsert.run(id, paper.key, c.id, Math.max(1, Math.round(entry.count / hit.length)), 'europepmc');
            report.mentions += 1;
          }
        }
        db.prepare('UPDATE papers SET annotated_at = ? WHERE key = ?').run(now, paper.key);
        db.exec('COMMIT');
      } catch (error) {
        db.exec('ROLLBACK');
        throw error;
      }
      report.annotated.push(paper.key);
      log(`annotated ${paper.key}  ${seen.size} terms`);
    }
  } finally {
    db.close();
  }
  return report;
}
