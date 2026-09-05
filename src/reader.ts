/**
 * One paper, read back whole — the payload the app's reader renders from
 * (projtracker issue #48, filed here as issue #5).
 *
 * Everything is rows the store already holds: the paper's own row, its
 * sections in their printed order, the chunks under them, entity mentions
 * pinned to chunks (so a reader can highlight names in the text), and the
 * model stage's rows with their sentences — the marginalia. Nothing here is
 * derived or summarized; the reader renders the record.
 */

import type { DatabaseSync } from 'node:sqlite';

export interface PaperView {
  paper: {
    key: string;
    title: string;
    year: number | null;
    journal: string | null;
    authors: string | null;
    abstract: string | null;
    doi: string | null;
    pmid: string | null;
    status: string;
    file: string | null;
    pub_type: string | null;
    ingested_at: string | null;
    extracted_at: string | null;
    annotated_at: string | null;
  };
  sections: { id: number; ordinal: number; heading: string; kind: string; page: number | null }[];
  chunks: { id: number; section: number; ordinal: number; page: number | null; text: string }[];
  /** Entity mentions pinned to chunks; chunk is null for paper-level terms. */
  mentions: { chunk: number | null; entity: number; name: string; kind: string; count: number }[];
  claims: { id: number; section: number; text: string; kind: string }[];
  materials: { id: number; section: number; name: string; role: string; amount: string | null }[];
  methods: { id: number; section: number; name: string; description: string }[];
  parameters: {
    id: number;
    section: number;
    chunk: number | null;
    value: string;
    value_num: number | null;
    unit: string;
    kind: string;
    sentence: string;
    entity: string | null;
  }[];
  refs: { ordinal: number; title: string; doi: string | null; pmid: string | null; matched_paper: string | null }[];
}

export function paperView(db: DatabaseSync, key: string): PaperView | undefined {
  const paper = db
    .prepare(
      `SELECT key, title, year, journal, authors, abstract, doi, pmid, status, file, pub_type,
              ingested_at, extracted_at, annotated_at
         FROM papers WHERE key = ?`,
    )
    .get(key) as PaperView['paper'] | undefined;
  if (!paper) return undefined;

  const rows = <T>(sql: string): T[] => db.prepare(sql).all(key) as T[];
  return {
    paper,
    sections: rows(
      'SELECT id, ordinal, heading, kind, page FROM sections WHERE paper = ? ORDER BY ordinal',
    ),
    chunks: rows(
      'SELECT id, section, ordinal, page, text FROM chunks WHERE paper = ? ORDER BY section, ordinal',
    ),
    mentions: rows(
      `SELECT m.chunk, m.entity, e.name, e.kind, SUM(m.count) AS count
         FROM mentions m JOIN entities e ON e.id = m.entity
        WHERE m.paper = ? GROUP BY m.chunk, m.entity ORDER BY m.chunk, e.name`,
    ),
    claims: rows('SELECT id, section, text, kind FROM claims WHERE paper = ? ORDER BY section, id'),
    materials: rows('SELECT id, section, name, role, amount FROM materials WHERE paper = ? ORDER BY section, id'),
    methods: rows('SELECT id, section, name, description FROM methods WHERE paper = ? ORDER BY section, id'),
    parameters: rows(
      `SELECT id, section, chunk, value, value_num, unit, kind, sentence, entity
         FROM parameters WHERE paper = ? ORDER BY section, id`,
    ),
    refs: rows('SELECT ordinal, title, doi, pmid, matched_paper FROM refs WHERE paper = ? ORDER BY ordinal'),
  };
}
