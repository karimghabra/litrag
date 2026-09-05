/**
 * The store: SQLite, rows first, vectors beside them.
 *
 * Everything the pipeline learns about a paper lands here in tables a person
 * can query — `lit sql` is a first-class read, not a debugging aid. The FTS5
 * index and the float32 vectors serve `lit query`; the rows serve questions
 * with columns in them.
 */

import { DatabaseSync } from 'node:sqlite';

export type PaperStatus = 'candidate' | 'fetched' | 'needs-pdf' | 'ingested';

export interface PaperInput {
  doi?: string;
  pmid?: string;
  pmcid?: string;
  title: string;
  year?: number;
  journal?: string;
  authors?: string;
  abstract?: string;
  source: string;
  openAccess?: boolean;
  citedByCount?: number;
  /** Europe PMC's publication types: "research-article; journal article", "review-article; …". */
  pubType?: string;
}

export interface PaperRow {
  key: string;
  doi: string | null;
  pmid: string | null;
  pmcid: string | null;
  title: string;
  year: number | null;
  journal: string | null;
  authors: string | null;
  abstract: string | null;
  source: string;
  open_access: number;
  status: PaperStatus;
  file: string | null;
  sha256: string | null;
  cited_by_count: number | null;
  added_at: string;
  ingested_at: string | null;
  extracted_with: string | null;
  extracted_at: string | null;
  pub_type: string | null;
  annotated_at: string | null;
}

export interface SectionInput {
  heading: string;
  kind: string;
  text: string;
  page?: number;
}

export interface ChunkInput {
  sectionOrdinal: number;
  ordinal: number;
  text: string;
  words: number;
  page?: number;
}

export interface ParameterInput {
  sectionOrdinal: number;
  chunkOrdinal?: number;
  value: string;
  valueNum: number | null;
  unit: string;
  kind: string;
  sentence: string;
}

export interface ReferenceInput {
  title: string;
  doi?: string;
  pmid?: string;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS papers (
  key TEXT PRIMARY KEY,
  doi TEXT, pmid TEXT, pmcid TEXT,
  title TEXT NOT NULL,
  year INTEGER, journal TEXT, authors TEXT, abstract TEXT,
  source TEXT NOT NULL,
  open_access INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'candidate',
  file TEXT, sha256 TEXT,
  cited_by_count INTEGER,
  added_at TEXT NOT NULL,
  ingested_at TEXT,
  extracted_with TEXT,
  extracted_at TEXT,
  pub_type TEXT,
  annotated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS papers_doi ON papers(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS papers_pmid ON papers(pmid) WHERE pmid IS NOT NULL;
CREATE INDEX IF NOT EXISTS papers_status ON papers(status);

CREATE TABLE IF NOT EXISTS sections (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  heading TEXT NOT NULL,
  kind TEXT NOT NULL,
  page INTEGER,
  text TEXT NOT NULL,
  UNIQUE(paper, ordinal)
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  page INTEGER,
  words INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_paper ON chunks(paper);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TABLE IF NOT EXISTS vectors (
  chunk INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dims INTEGER NOT NULL,
  vec BLOB NOT NULL,
  PRIMARY KEY(chunk, model)
);

CREATE TABLE IF NOT EXISTS parameters (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  chunk INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
  value TEXT NOT NULL,
  value_num REAL,
  unit TEXT NOT NULL,
  kind TEXT NOT NULL,
  sentence TEXT NOT NULL,
  entity TEXT,
  source TEXT NOT NULL DEFAULT 'miner'
);
CREATE INDEX IF NOT EXISTS parameters_kind ON parameters(kind, unit);

CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  kind TEXT NOT NULL,
  source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS materials (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  amount TEXT,
  source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS methods (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refs (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  title TEXT NOT NULL,
  doi TEXT, pmid TEXT,
  matched_paper TEXT
);

CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  norm TEXT NOT NULL,
  kind TEXT NOT NULL,
  UNIQUE(norm, kind)
);
CREATE TABLE IF NOT EXISTS mentions (
  entity INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  chunk INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
  count INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL,
  PRIMARY KEY(entity, paper, chunk, source)
);
CREATE INDEX IF NOT EXISTS mentions_chunk ON mentions(chunk);
CREATE INDEX IF NOT EXISTS mentions_paper ON mentions(paper);

CREATE TABLE IF NOT EXISTS queries (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  query TEXT NOT NULL,
  last_run TEXT,
  hits INTEGER,
  UNIQUE(source, query)
);

-- The reader's marginalia (#10). chunk carries no foreign key on purpose:
-- a --reread retires chunk ids, and the quote re-anchors the note to the
-- passage's text instead of losing it.
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  chunk INTEGER,
  quote TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_paper ON notes(paper);
`;

export function openDb(path: string, options: { readOnly?: boolean } = {}): DatabaseSync {
  const db = new DatabaseSync(path, { readOnly: options.readOnly ?? false });
  if (!options.readOnly) {
    db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;');
    db.exec(SCHEMA);
    migrate(db);
  }
  return db;
}

/** Columns added after the first stores were written; each is added once. */
function migrate(db: DatabaseSync): void {
  const version = (db.prepare('PRAGMA user_version').get() as { user_version: number }).user_version;
  if (version < 2) {
    const has = (table: string, column: string) =>
      (db.prepare(`PRAGMA table_info(${table})`).all() as { name: string }[]).some((c) => c.name === column);
    if (!has('parameters', 'entity')) db.exec('ALTER TABLE parameters ADD COLUMN entity TEXT');
    if (!has('parameters', 'source')) db.exec("ALTER TABLE parameters ADD COLUMN source TEXT NOT NULL DEFAULT 'miner'");
    if (!has('papers', 'extracted_with')) db.exec('ALTER TABLE papers ADD COLUMN extracted_with TEXT');
    if (!has('papers', 'extracted_at')) db.exec('ALTER TABLE papers ADD COLUMN extracted_at TEXT');
    db.exec('PRAGMA user_version = 2');
  }
  if (version < 3) {
    const has = (table: string, column: string) =>
      (db.prepare(`PRAGMA table_info(${table})`).all() as { name: string }[]).some((c) => c.name === column);
    if (!has('papers', 'pub_type')) db.exec('ALTER TABLE papers ADD COLUMN pub_type TEXT');
    if (!has('papers', 'annotated_at')) db.exec('ALTER TABLE papers ADD COLUMN annotated_at TEXT');
    db.exec('PRAGMA user_version = 3');
  }
}

/** The identity a paper is filed under: DOI, then PMID, then PMCID, then its bytes. */
export function paperKey(p: { doi?: string | null; pmid?: string | null; pmcid?: string | null; sha256?: string | null }): string {
  if (p.doi) return `doi:${p.doi.toLowerCase()}`;
  if (p.pmid) return `pmid:${p.pmid}`;
  if (p.pmcid) return `pmcid:${p.pmcid.toUpperCase()}`;
  if (p.sha256) return `sha:${p.sha256.slice(0, 16)}`;
  throw new Error('A paper needs a DOI, a PMID, a PMCID or a file to be filed under.');
}

export function normalizeDoi(doi: string | undefined | null): string | undefined {
  if (!doi) return undefined;
  const clean = doi.trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '').toLowerCase();
  return clean || undefined;
}

/**
 * File a paper, or fill in what an earlier filing lacked. Never demotes a
 * status: a second search hit for an ingested paper leaves it ingested.
 */
export function upsertPaper(db: DatabaseSync, input: PaperInput, now: string, sha256?: string): { key: string; created: boolean } {
  const doi = normalizeDoi(input.doi);
  const existing = db
    .prepare(
      `SELECT * FROM papers WHERE (doi IS NOT NULL AND doi = ?) OR (pmid IS NOT NULL AND pmid = ?)
         OR (pmcid IS NOT NULL AND pmcid = ?) OR (sha256 IS NOT NULL AND sha256 = ?) LIMIT 1`,
    )
    .get(doi ?? '', input.pmid ?? '', input.pmcid?.toUpperCase() ?? '', sha256 ?? '') as PaperRow | undefined;
  if (existing) {
    db.prepare(
      `UPDATE papers SET doi = COALESCE(doi, ?), pmid = COALESCE(pmid, ?), pmcid = COALESCE(pmcid, ?),
         title = CASE WHEN title = '' OR title LIKE 'Untitled%' THEN ? ELSE title END,
         year = COALESCE(year, ?), journal = COALESCE(journal, ?), authors = COALESCE(authors, ?),
         abstract = COALESCE(abstract, ?), open_access = MAX(open_access, ?),
         cited_by_count = COALESCE(?, cited_by_count), sha256 = COALESCE(sha256, ?), pub_type = COALESCE(pub_type, ?)
       WHERE key = ?`,
    ).run(
      doi ?? null, input.pmid ?? null, input.pmcid?.toUpperCase() ?? null, input.title,
      input.year ?? null, input.journal ?? null, input.authors ?? null, input.abstract ?? null,
      input.openAccess ? 1 : 0, input.citedByCount ?? null, sha256 ?? null, input.pubType ?? null, existing.key,
    );
    return { key: existing.key, created: false };
  }
  const key = paperKey({ doi, pmid: input.pmid, pmcid: input.pmcid, sha256 });
  db.prepare(
    `INSERT INTO papers (key, doi, pmid, pmcid, title, year, journal, authors, abstract, source, open_access, status, sha256, cited_by_count, added_at, pub_type)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?)`,
  ).run(
    key, doi ?? null, input.pmid ?? null, input.pmcid?.toUpperCase() ?? null, input.title, input.year ?? null,
    input.journal ?? null, input.authors ?? null, input.abstract ?? null, input.source, input.openAccess ? 1 : 0,
    sha256 ?? null, input.citedByCount ?? null, now, input.pubType ?? null,
  );
  return { key, created: true };
}

export function paperByKey(db: DatabaseSync, key: string): PaperRow | undefined {
  return db.prepare('SELECT * FROM papers WHERE key = ?').get(key) as PaperRow | undefined;
}

export function papersByStatus(db: DatabaseSync, status: PaperStatus): PaperRow[] {
  return db.prepare('SELECT * FROM papers WHERE status = ? ORDER BY added_at, key').all(status) as unknown as PaperRow[];
}

export function allPapers(db: DatabaseSync): PaperRow[] {
  return db.prepare('SELECT * FROM papers ORDER BY year DESC, title').all() as unknown as PaperRow[];
}

export function setPaperFile(db: DatabaseSync, key: string, file: string, sha256: string | null, status: PaperStatus): void {
  db.prepare('UPDATE papers SET file = ?, sha256 = COALESCE(?, sha256), status = ? WHERE key = ?').run(file, sha256, status, key);
}

export function setPaperStatus(db: DatabaseSync, key: string, status: PaperStatus): void {
  db.prepare('UPDATE papers SET status = ? WHERE key = ?').run(status, key);
}

export interface Extraction {
  sections: SectionInput[];
  chunks: ChunkInput[];
  parameters: ParameterInput[];
  references: ReferenceInput[];
  /** Fields the extraction learned that the filing may have lacked. */
  meta?: Partial<PaperInput>;
}

/** Everything read from a paper, replaced as one transaction; vectors go with the chunks. */
export function replaceExtraction(db: DatabaseSync, key: string, extraction: Extraction, now: string): void {
  db.exec('BEGIN');
  try {
    db.prepare('DELETE FROM parameters WHERE paper = ?').run(key);
    db.prepare('DELETE FROM mentions WHERE paper = ?').run(key);
    db.prepare('DELETE FROM claims WHERE paper = ?').run(key);
    db.prepare('DELETE FROM materials WHERE paper = ?').run(key);
    db.prepare('DELETE FROM methods WHERE paper = ?').run(key);
    db.prepare('DELETE FROM refs WHERE paper = ?').run(key);
    db.prepare('DELETE FROM chunks WHERE paper = ?').run(key);
    db.prepare('DELETE FROM sections WHERE paper = ?').run(key);
    const sectionIds: number[] = [];
    const insertSection = db.prepare('INSERT INTO sections (paper, ordinal, heading, kind, page, text) VALUES (?, ?, ?, ?, ?, ?)');
    extraction.sections.forEach((s, i) => {
      const result = insertSection.run(key, i, s.heading, s.kind, s.page ?? null, s.text);
      sectionIds.push(Number(result.lastInsertRowid));
    });
    const chunkIds = new Map<string, number>();
    const insertChunk = db.prepare('INSERT INTO chunks (paper, section, ordinal, page, words, text) VALUES (?, ?, ?, ?, ?, ?)');
    for (const c of extraction.chunks) {
      const section = sectionIds[c.sectionOrdinal];
      if (section === undefined) continue;
      const result = insertChunk.run(key, section, c.ordinal, c.page ?? null, c.words, c.text);
      chunkIds.set(`${c.sectionOrdinal}:${c.ordinal}`, Number(result.lastInsertRowid));
    }
    const insertParameter = db.prepare('INSERT INTO parameters (paper, section, chunk, value, value_num, unit, kind, sentence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)');
    for (const p of extraction.parameters) {
      const section = sectionIds[p.sectionOrdinal];
      if (section === undefined) continue;
      const chunk = p.chunkOrdinal === undefined ? null : chunkIds.get(`${p.sectionOrdinal}:${p.chunkOrdinal}`) ?? null;
      insertParameter.run(key, section, chunk, p.value, p.valueNum, p.unit, p.kind, p.sentence);
    }
    const insertRef = db.prepare('INSERT INTO refs (paper, ordinal, title, doi, pmid, matched_paper) VALUES (?, ?, ?, ?, ?, ?)');
    extraction.references.forEach((r, i) => {
      const doi = normalizeDoi(r.doi);
      const matched = doi
        ? (db.prepare('SELECT key FROM papers WHERE doi = ?').get(doi) as { key: string } | undefined)?.key ?? null
        : r.pmid
          ? (db.prepare('SELECT key FROM papers WHERE pmid = ?').get(r.pmid) as { key: string } | undefined)?.key ?? null
          : null;
      insertRef.run(key, i, r.title, doi ?? null, r.pmid ?? null, matched);
    });
    const meta = extraction.meta;
    if (meta) {
      // An identifier another paper already holds is not this paper's to
      // take: a preprint and its published twin share a PMID or a DOI and
      // one of them would otherwise fail to read at all.
      const taken = (column: string, value: string | undefined) =>
        value !== undefined &&
        db.prepare(`SELECT 1 FROM papers WHERE ${column} = ? AND key != ?`).get(column === 'doi' ? normalizeDoi(value) ?? '' : column === 'pmcid' ? value.toUpperCase() : value, key) !== undefined;
      if (taken('doi', meta.doi)) meta.doi = undefined;
      if (taken('pmid', meta.pmid)) meta.pmid = undefined;
      if (taken('pmcid', meta.pmcid)) meta.pmcid = undefined;
      db.prepare(
        `UPDATE papers SET doi = COALESCE(doi, ?), pmid = COALESCE(pmid, ?), pmcid = COALESCE(pmcid, ?),
           title = CASE WHEN title = '' OR title LIKE 'Untitled%' OR title LIKE '%&lt;%' OR title LIKE '%<%' THEN COALESCE(?, title) ELSE title END,
           year = COALESCE(year, ?), journal = COALESCE(journal, ?), authors = COALESCE(authors, ?), abstract = COALESCE(abstract, ?)
         WHERE key = ?`,
      ).run(
        normalizeDoi(meta.doi) ?? null, meta.pmid ?? null, meta.pmcid?.toUpperCase() ?? null, meta.title ?? null,
        meta.year ?? null, meta.journal ?? null, meta.authors ?? null, meta.abstract ?? null, key,
      );
    }
    db.prepare("UPDATE papers SET status = 'ingested', ingested_at = ?, extracted_with = NULL, extracted_at = NULL, annotated_at = NULL WHERE key = ?").run(now, key);
    db.exec('COMMIT');
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

export function chunksWithoutVectors(db: DatabaseSync, model: string): { id: number; text: string }[] {
  return db
    .prepare('SELECT c.id, c.text FROM chunks c LEFT JOIN vectors v ON v.chunk = c.id AND v.model = ? WHERE v.chunk IS NULL ORDER BY c.id')
    .all(model) as { id: number; text: string }[];
}

export function insertVector(db: DatabaseSync, chunk: number, model: string, vec: Float32Array): void {
  const bytes = new Uint8Array(vec.buffer, vec.byteOffset, vec.byteLength);
  db.prepare('INSERT OR REPLACE INTO vectors (chunk, model, dims, vec) VALUES (?, ?, ?, ?)').run(chunk, model, vec.length, bytes);
}

export function allVectors(db: DatabaseSync, model: string): { chunk: number; vec: Float32Array }[] {
  const rows = db.prepare('SELECT chunk, dims, vec FROM vectors WHERE model = ?').all(model) as { chunk: number; dims: number; vec: Uint8Array }[];
  return rows.map((r) => {
    const copy = new Uint8Array(r.vec);
    return { chunk: r.chunk, vec: new Float32Array(copy.buffer, 0, r.dims) };
  });
}

/** A question as FTS5 wants it: the words, quoted, any of them. */
export function ftsQuery(question: string): string {
  const words = question
    .toLowerCase()
    .split(/[^a-z0-9µ.%/-]+/)
    .map((w) => w.replace(/^[.\-/]+|[.\-/]+$/g, ''))
    .filter((w) => w.length > 1 && !STOP.has(w));
  return [...new Set(words)].map((w) => `"${w.replace(/"/g, '')}"`).join(' OR ');
}

const STOP = new Set(['the', 'a', 'an', 'of', 'in', 'on', 'for', 'to', 'and', 'or', 'is', 'are', 'was', 'were', 'be', 'by', 'with', 'as', 'at', 'from', 'that', 'this', 'it', 'its', 'has', 'have', 'had', 'do', 'does', 'did', 'what', 'which', 'who', 'how', 'why', 'when', 'where', 'any', 'anyone', 'used', 'use', 'using', 'been', 'than', 'into', 'their', 'there']);

export function ftsSearch(db: DatabaseSync, question: string, limit: number): { chunk: number; score: number }[] {
  const match = ftsQuery(question);
  if (!match) return [];
  const rows = db
    .prepare('SELECT rowid AS chunk, bm25(chunks_fts) AS score FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?')
    .all(match, limit) as { chunk: number; score: number }[];
  // bm25 is "lower is better"; hand back "higher is better" so every ranking reads one way.
  return rows.map((r) => ({ chunk: r.chunk, score: -r.score }));
}

export interface ChunkView {
  id: number;
  paper: string;
  title: string;
  year: number | null;
  journal: string | null;
  doi: string | null;
  heading: string;
  kind: string;
  page: number | null;
  text: string;
}

export function chunkViews(db: DatabaseSync, ids: number[]): Map<number, ChunkView> {
  const out = new Map<number, ChunkView>();
  if (!ids.length) return out;
  const stmt = db.prepare(
    `SELECT c.id, c.paper, p.title, p.year, p.journal, p.doi, s.heading, s.kind, c.page, c.text
       FROM chunks c JOIN papers p ON p.key = c.paper JOIN sections s ON s.id = c.section WHERE c.id = ?`,
  );
  for (const id of ids) {
    const row = stmt.get(id) as ChunkView | undefined;
    if (row) out.set(id, row);
  }
  return out;
}

export interface StatusView {
  papers: Record<PaperStatus, number>;
  chunks: number;
  vectors: number;
  parameters: number;
  claims: number;
  materials: number;
  methods: number;
  extracted: number;
  annotated: number;
  entities: number;
  mentions: number;
  needsPdf: { key: string; title: string; year: number | null; doi: string | null }[];
}

export function statusView(db: DatabaseSync, model: string): StatusView {
  const papers: Record<PaperStatus, number> = { candidate: 0, fetched: 0, 'needs-pdf': 0, ingested: 0 };
  for (const row of db.prepare('SELECT status, COUNT(*) n FROM papers GROUP BY status').all() as { status: PaperStatus; n: number }[]) {
    papers[row.status] = row.n;
  }
  const count = (sql: string, ...args: (string | number)[]) => (db.prepare(sql).get(...args) as { n: number }).n;
  return {
    papers,
    chunks: count('SELECT COUNT(*) n FROM chunks'),
    vectors: count('SELECT COUNT(*) n FROM vectors WHERE model = ?', model),
    parameters: count('SELECT COUNT(*) n FROM parameters'),
    claims: count('SELECT COUNT(*) n FROM claims'),
    materials: count('SELECT COUNT(*) n FROM materials'),
    methods: count('SELECT COUNT(*) n FROM methods'),
    extracted: count('SELECT COUNT(*) n FROM papers WHERE extracted_with IS NOT NULL'),
    annotated: count('SELECT COUNT(*) n FROM papers WHERE annotated_at IS NOT NULL'),
    entities: count('SELECT COUNT(*) n FROM entities'),
    mentions: count('SELECT COUNT(*) n FROM mentions'),
    needsPdf: db.prepare("SELECT key, title, year, doi FROM papers WHERE status = 'needs-pdf' ORDER BY year DESC").all() as StatusView['needsPdf'],
  };
}

export interface SectionRow {
  id: number;
  ordinal: number;
  heading: string;
  kind: string;
  text: string;
}

export function sectionsOf(db: DatabaseSync, key: string): SectionRow[] {
  return db.prepare('SELECT id, ordinal, heading, kind, text FROM sections WHERE paper = ? ORDER BY ordinal').all(key) as unknown as SectionRow[];
}

export interface NoteRow {
  id: number;
  paper: string;
  /** The chunk the note is anchored to now — re-anchored by quote when the
   * original id died in a reread; null when the quote is gone too. */
  chunk: number | null;
  quote: string;
  text: string;
  createdAt: string;
}

/**
 * Attach a note to a chunk (#10): the chunk must belong to the paper, and a
 * quote of its opening text is stored so the note survives a --reread.
 */
export function attachNote(db: DatabaseSync, paper: string, chunk: number, text: string, now: string): NoteRow {
  const row = db.prepare('SELECT text FROM chunks WHERE id = ? AND paper = ?').get(chunk, paper) as { text: string } | undefined;
  if (!row) throw new Error(`Paper "${paper}" has no chunk ${chunk}.`);
  const quote = row.text.slice(0, 160);
  const result = db
    .prepare('INSERT INTO notes (paper, chunk, quote, text, created_at) VALUES (?, ?, ?, ?, ?)')
    .run(paper, chunk, quote, text, now);
  return { id: Number(result.lastInsertRowid), paper, chunk, quote, text, createdAt: now };
}

export function deleteNote(db: DatabaseSync, id: number): boolean {
  return db.prepare('DELETE FROM notes WHERE id = ?').run(id).changes > 0;
}

/** A paper's notes, each re-anchored to a live chunk by its quote if the
 * stored id has died; a note whose passage is gone shows at paper level. */
export function notesOf(db: DatabaseSync, paper: string): NoteRow[] {
  let rows: NoteRow[];
  try {
    rows = db
      .prepare('SELECT id, paper, chunk, quote, text, created_at createdAt FROM notes WHERE paper = ? ORDER BY id')
      .all(paper) as unknown as NoteRow[];
  } catch {
    // A store opened read-only before any note was ever written has no
    // notes table yet; that is an empty answer, not an error.
    return [];
  }
  const alive = new Set(
    (db.prepare('SELECT id FROM chunks WHERE paper = ?').all(paper) as { id: number }[]).map((c) => c.id),
  );
  const anchor = db.prepare('SELECT id FROM chunks WHERE paper = ? AND instr(text, ?) > 0 LIMIT 1');
  return rows.map((note) => {
    if (note.chunk !== null && alive.has(note.chunk)) return note;
    const found = anchor.get(note.paper, note.quote) as { id: number } | undefined;
    return { ...note, chunk: found?.id ?? null };
  });
}

export interface PaperPayload {
  paper: {
    key: string;
    title: string;
    year: number | null;
    journal: string | null;
    authors: string | null;
    abstract: string | null;
    doi: string | null;
    status: string;
    file: string | null;
  };
  sections: { id: number; ordinal: number; heading: string; kind: string }[];
  chunks: { id: number; section: number; ordinal: number; text: string }[];
  mentions: { chunk: number | null; entity: number; name: string; kind: string; count: number }[];
  claims: { id: number; section: number; text: string; kind: string }[];
  materials: { id: number; section: number; name: string; role: string; amount: string | null }[];
  methods: { id: number; section: number; name: string; description: string }[];
  parameters: { id: number; section: number; value: string; unit: string; kind: string; sentence: string; entity: string | null }[];
  notes: NoteRow[];
}

/**
 * The whole reader in one answer (#5): the paper row, its sections in printed
 * order, their chunks, entity mentions pinned to chunks (chunk NULL means the
 * whole paper), and the model stage's rows. One call because a reader should
 * not be a query engine.
 */
export function paperPayload(db: DatabaseSync, key: string): PaperPayload | undefined {
  const paper = db
    .prepare('SELECT key, title, year, journal, authors, abstract, doi, status, file FROM papers WHERE key = ?')
    .get(key) as PaperPayload['paper'] | undefined;
  if (!paper) return undefined;
  return {
    paper,
    sections: db.prepare('SELECT id, ordinal, heading, kind FROM sections WHERE paper = ? ORDER BY ordinal').all(key) as unknown as PaperPayload['sections'],
    chunks: db.prepare('SELECT id, section, ordinal, text FROM chunks WHERE paper = ? ORDER BY section, ordinal').all(key) as unknown as PaperPayload['chunks'],
    mentions: db
      .prepare(
        'SELECT m.chunk, m.entity, e.name, e.kind, SUM(m.count) count FROM mentions m JOIN entities e ON e.id = m.entity WHERE m.paper = ? GROUP BY m.entity, m.chunk',
      )
      .all(key) as unknown as PaperPayload['mentions'],
    claims: db.prepare('SELECT id, section, text, kind FROM claims WHERE paper = ? ORDER BY id').all(key) as unknown as PaperPayload['claims'],
    materials: db.prepare('SELECT id, section, name, role, amount FROM materials WHERE paper = ? ORDER BY id').all(key) as unknown as PaperPayload['materials'],
    methods: db.prepare('SELECT id, section, name, description FROM methods WHERE paper = ? ORDER BY id').all(key) as unknown as PaperPayload['methods'],
    parameters: db
      .prepare('SELECT id, section, value, unit, kind, sentence, entity FROM parameters WHERE paper = ? ORDER BY id')
      .all(key) as unknown as PaperPayload['parameters'],
    notes: notesOf(db, key),
  };
}

export function papersToExtract(db: DatabaseSync, model: string): PaperRow[] {
  return db
    .prepare("SELECT * FROM papers WHERE status = 'ingested' AND (extracted_with IS NULL OR extracted_with != ?) ORDER BY added_at, key")
    .all(model) as unknown as PaperRow[];
}

export interface ModelRows {
  claims: { text: string; kind: string }[];
  materials: { name: string; role: string; amount?: string }[];
  methods: { name: string; description: string }[];
  parameters: { entity: string; value: string; unit: string; context: string }[];
}

/** The model's rows for one paper, replacing any earlier model's, as one transaction. */
export function replaceModelRows(db: DatabaseSync, key: string, model: string, rows: { section: number; rows: ModelRows }[], now: string): void {
  db.exec('BEGIN');
  try {
    db.prepare("DELETE FROM claims WHERE paper = ?").run(key);
    db.prepare("DELETE FROM materials WHERE paper = ?").run(key);
    db.prepare("DELETE FROM methods WHERE paper = ?").run(key);
    db.prepare("DELETE FROM parameters WHERE paper = ? AND source != 'miner'").run(key);
    db.prepare("DELETE FROM mentions WHERE paper = ? AND source = 'model'").run(key);
    const claim = db.prepare('INSERT INTO claims (paper, section, text, kind, source) VALUES (?, ?, ?, ?, ?)');
    const material = db.prepare('INSERT INTO materials (paper, section, name, role, amount, source) VALUES (?, ?, ?, ?, ?, ?)');
    const method = db.prepare('INSERT INTO methods (paper, section, name, description, source) VALUES (?, ?, ?, ?, ?)');
    const parameter = db.prepare('INSERT INTO parameters (paper, section, chunk, value, value_num, unit, kind, sentence, entity, source) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)');
    for (const { section, rows: r } of rows) {
      for (const c of r.claims) claim.run(key, section, c.text, c.kind, model);
      for (const m of r.materials) {
        material.run(key, section, m.name, m.role, m.amount ?? null, model);
        mention(db, key, section, m.name, 'material', 'model');
      }
      for (const m of r.methods) {
        method.run(key, section, m.name, m.description, model);
        mention(db, key, section, m.name, 'method', 'model');
      }
      for (const p of r.parameters) {
        const n = Number(String(p.value).replace(',', '.').match(/-?\d+(?:\.\d+)?/)?.[0]);
        parameter.run(key, section, p.value, Number.isFinite(n) ? n : null, p.unit, 'model', p.context, p.entity, model);
      }
    }
    db.prepare('UPDATE papers SET extracted_with = ?, extracted_at = ? WHERE key = ?').run(model, now, key);
    db.exec('COMMIT');
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

/** A name as the graph files it: case, spacing and a trailing plural set aside. */
export function normalizeEntity(name: string): string {
  const n = name.toLowerCase().replace(/\s+/g, ' ').trim();
  return n.length > 4 && n.endsWith('s') && !n.endsWith('ss') ? n.slice(0, -1) : n;
}

export function entityId(db: DatabaseSync, name: string, kind: string): number {
  const norm = normalizeEntity(name);
  if (!norm) throw new Error('An entity needs a name.');
  const found = db.prepare('SELECT id FROM entities WHERE norm = ? AND kind = ?').get(norm, kind) as { id: number } | undefined;
  if (found) return found.id;
  return Number(db.prepare('INSERT INTO entities (name, norm, kind) VALUES (?, ?, ?)').run(name.trim(), norm, kind).lastInsertRowid);
}

/**
 * A mention of an entity in a paper, pinned to the chunks of the section that
 * name it — or to the section's first chunk when the name is only implied.
 */
export function mention(db: DatabaseSync, paper: string, section: number, name: string, kind: string, source: string): void {
  if (!name.trim()) return;
  const id = entityId(db, name, kind);
  const chunks = db.prepare('SELECT id, text FROM chunks WHERE paper = ? AND section = ? ORDER BY ordinal').all(paper, section) as { id: number; text: string }[];
  const needle = name.trim().toLowerCase();
  let hit = chunks.filter((c) => c.text.toLowerCase().includes(needle)).map((c) => c.id);
  if (!hit.length) hit = chunks.slice(0, 1).map((c) => c.id);
  const upsert = db.prepare('INSERT INTO mentions (entity, paper, chunk, count, source) VALUES (?, ?, ?, 1, ?) ON CONFLICT(entity, paper, chunk, source) DO UPDATE SET count = count + 1');
  if (!hit.length) upsert.run(id, paper, null, source);
  for (const chunk of hit) upsert.run(id, paper, chunk, source);
}
