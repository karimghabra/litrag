/**
 * Reading the store: hybrid retrieval, and SQL by hand.
 *
 * A question goes through the words (FTS5, bm25) and through the meaning
 * (cosine over the vectors), and the two rankings are fused by reciprocal
 * rank so a chunk near the top of either is near the top of the answer.
 * Included libraries — the shared spine — are searched the same way and
 * every hit says which library it came from. The tool returns chunks with
 * citations; the prose is the assistant's to write.
 */

import { DatabaseSync } from 'node:sqlite';
import { allVectors, chunkViews, ftsSearch, openDb } from './db.ts';
import { cosine, type Embedder } from './embed.ts';
import { buildGraph, graphSearch } from './graph.ts';
import { openLibrary, type Library } from './library.ts';

export interface QueryHit {
  library: string;
  chunk: number;
  paper: string;
  title: string;
  year: number | null;
  journal: string | null;
  doi: string | null;
  section: string;
  kind: string;
  page: number | null;
  text: string;
  score: number;
  /** How the chunk earned its place: which rankings held it, and where. */
  ranks: { words?: number; meaning?: number; graph?: number };
  citation: string;
}

export interface QueryTrace {
  /** The entities the graph walk started from, per library. */
  seeds: Record<string, string[]>;
}

const RRF_K = 60;

function citationFor(hit: { title: string; year: number | null; journal: string | null; doi: string | null; section: string; page: number | null }): string {
  const where = [hit.section, hit.page ? `p. ${hit.page}` : ''].filter(Boolean).join(', ');
  return `${hit.title}${hit.year ? ` (${hit.year})` : ''}${hit.journal ? `, ${hit.journal}` : ''}${hit.doi ? `, doi:${hit.doi}` : ''} — ${where}`;
}

async function queryOne(lib: Library, question: string, embedder: Embedder, perList: number, useGraph: boolean, trace: QueryTrace): Promise<QueryHit[]> {
  const db = openDb(lib.dbPath, { readOnly: true });
  try {
    const words = ftsSearch(db, question, perList);
    const qv = await embedder.embedQuery(question);
    const vectors = allVectors(db, embedder.model);
    const meaning = vectors
      .map((v) => ({ chunk: v.chunk, score: cosine(qv, v.vec) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, perList);
    // The graph: seeded from the question's entities and the nearest passages,
    // it reaches passages that share those entities without sharing the words.
    let walk: { chunk: number; score: number }[] = [];
    if (useGraph) {
      const graph = buildGraph(db);
      const result = graphSearch(graph, question, meaning.slice(0, 10).map((m) => m.chunk), perList);
      walk = result.hits;
      trace.seeds[lib.manifest.id] = result.seeds;
    }
    const fused = new Map<number, { score: number; ranks: QueryHit['ranks'] }>();
    const take = (list: { chunk: number }[], name: keyof QueryHit['ranks']) => {
      list.forEach((item, i) => {
        const entry = fused.get(item.chunk) ?? { score: 0, ranks: {} };
        entry.score += 1 / (RRF_K + i + 1);
        entry.ranks[name] = i + 1;
        fused.set(item.chunk, entry);
      });
    };
    take(words, 'words');
    take(meaning, 'meaning');
    take(walk, 'graph');
    const views = chunkViews(db, [...fused.keys()]);
    const out: QueryHit[] = [];
    for (const [chunk, { score, ranks }] of fused) {
      const view = views.get(chunk);
      if (!view) continue;
      const hit = {
        library: lib.manifest.id,
        chunk,
        paper: view.paper,
        title: view.title,
        year: view.year,
        journal: view.journal,
        doi: view.doi,
        section: view.heading,
        kind: view.kind,
        page: view.page,
        text: view.text,
        score,
        ranks,
      };
      out.push({ ...hit, citation: citationFor(hit) });
    }
    return out;
  } finally {
    db.close();
  }
}

export interface QueryOptions {
  limit?: number;
  /** Search the libraries the manifest includes too. Default true. */
  spine?: boolean;
  /** Walk the entity graph as a third ranking. Default true. */
  graph?: boolean;
  /** Filled in with what the graph walk started from. */
  trace?: QueryTrace;
  /**
   * Scope the question to one paper by its key — "what does *this* paper say"
   * (issue #5, for the app's reader). The whole ranking still runs, so the
   * best passages of that paper surface in the same order they would in the
   * library-wide answer; the spine is skipped because a key names a paper in
   * this library.
   */
  paper?: string;
}

export async function queryLibrary(lib: Library, question: string, embedder: Embedder, options: QueryOptions = {}): Promise<QueryHit[]> {
  const limit = options.limit ?? 8;
  const trace = options.trace ?? { seeds: {} };
  const libs: Library[] = [lib];
  if (options.spine !== false && !options.paper) {
    for (const key of lib.manifest.includes) {
      const included = openLibrary(lib.root, key);
      if (included && included.manifest.id !== lib.manifest.id) libs.push(included);
    }
  }
  // A paper-scoped question needs a wider net before the filter, or a paper
  // that is merely not in the global top-20 reads as having nothing to say.
  const perList = options.paper ? Math.max(200, limit * 25) : Math.max(20, limit * 3);
  const hits: QueryHit[] = [];
  for (const l of libs) hits.push(...(await queryOne(l, question, embedder, perList, options.graph !== false, trace)));
  const scoped = options.paper ? hits.filter((h) => h.paper === options.paper) : hits;
  return scoped.sort((a, b) => b.score - a.score).slice(0, limit);
}

/** A SELECT, and only a SELECT, against a read-only handle. */
export function runSql(lib: Library, sql: string, limit = 200): { columns: string[]; rows: Record<string, unknown>[] } {
  const statement = sql.trim().replace(/;\s*$/, '');
  // A semicolon inside a string literal is the literal's business.
  const bare = statement.replace(/'(?:[^']|'')*'/g, "''").replace(/"(?:[^"]|"")*"/g, '""');
  if (!/^(select|with)\b/i.test(statement) || bare.includes(';')) {
    throw new Error('lit sql runs one SELECT (or WITH … SELECT); nothing else.');
  }
  const db = new DatabaseSync(lib.dbPath, { readOnly: true });
  try {
    const rows = db.prepare(`SELECT * FROM (${statement}) LIMIT ${Math.max(1, limit)}`).all() as Record<string, unknown>[];
    const columns = rows[0] ? Object.keys(rows[0]) : [];
    return { columns, rows };
  } finally {
    db.close();
  }
}
