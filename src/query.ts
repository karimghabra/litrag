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
import { listLibraries, openLibrary, type Library } from './library.ts';

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
  /** How the hit earned its place: which rankings held it, and where. */
  ranks: { words?: number; meaning?: number; graph?: number; facts?: number };
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

/** The question's salient words: long enough to mean something, deduplicated. */
function questionTerms(question: string): string[] {
  const stop = new Set(['about', 'after', 'before', 'between', 'could', 'does', 'point', 'should', 'their', 'there', 'these', 'value', 'values', 'what', 'when', 'where', 'which', 'would']);
  return [
    ...new Set(
      question
        .toLowerCase()
        .replace(/[^a-z0-9µ%/.-]+/g, ' ')
        .split(' ')
        .filter((w) => w.length >= 5 && !stop.has(w)),
    ),
  ];
}

/**
 * The facts pass (#9): the model stage's parameters and claims are the best-
 * structured knowledge in the store, and a bench question is often a
 * parameter question — so rows whose kind, entity, sentence or text carry
 * the question's words lead the answer, cited like any chunk.
 */
function factsFor(lib: Library, question: string, limit: number): QueryHit[] {
  const terms = questionTerms(question);
  if (!terms.length) return [];
  const db = openDb(lib.dbPath, { readOnly: true });
  try {
    // Matched = how many of the question's terms the row carries anywhere.
    const termCount = (hay: string) => terms.map(() => `(instr(${hay}, ?) > 0)`).join(' + ');
    const enough = Math.min(2, terms.length);
    const params = db
      .prepare(
        `SELECT pr.value, pr.unit, pr.kind, pr.entity, pr.sentence, pr.chunk, pr.paper, p.title, p.year, p.journal, p.doi, s.heading, s.kind skind, s.page,
                (${termCount("lower(pr.kind || ' ' || coalesce(pr.entity, '') || ' ' || pr.sentence)")}) matched
           FROM parameters pr JOIN papers p ON p.key = pr.paper JOIN sections s ON s.id = pr.section
          WHERE pr.value != 'not specified'
          ORDER BY matched DESC LIMIT 200`,
      )
      .all(...terms) as {
      value: string; unit: string; kind: string; entity: string | null; sentence: string; chunk: number | null;
      paper: string; title: string; year: number | null; journal: string | null; doi: string | null;
      heading: string; skind: string; page: number | null; matched: number;
    }[];
    const claims = db
      .prepare(
        `SELECT c.text, c.paper, p.title, p.year, p.journal, p.doi, s.heading, s.kind skind, s.page,
                (${termCount('lower(c.text)')}) matched
           FROM claims c JOIN papers p ON p.key = c.paper JOIN sections s ON s.id = c.section
          ORDER BY matched DESC LIMIT 200`,
      )
      .all(...terms) as {
      text: string; paper: string; title: string; year: number | null; journal: string | null; doi: string | null;
      heading: string; skind: string; page: number | null; matched: number;
    }[];
    const scored: { matched: number; text: string; row: { paper: string; title: string; year: number | null; journal: string | null; doi: string | null; heading: string; skind: string; page: number | null; chunk?: number | null } }[] = [
      // A measured value outranks a prose claim at equal term coverage.
      ...params.filter((r) => r.matched >= enough).map((r) => ({
        matched: r.matched + 0.5,
        text: `${r.value} ${r.unit} — ${r.entity ? `${r.entity}: ` : ''}${r.sentence}`,
        row: r,
      })),
      ...claims.filter((r) => r.matched >= enough).map((r) => ({
        matched: r.matched,
        text: r.text,
        row: r,
      })),
    ];
    scored.sort((a, b) => b.matched - a.matched);
    return scored.slice(0, limit).map(({ text, row }, i) => {
      const hit = {
        library: lib.manifest.id,
        chunk: row.chunk ?? -1,
        paper: row.paper,
        title: row.title,
        year: row.year,
        journal: row.journal,
        doi: row.doi,
        section: row.heading,
        kind: row.skind,
        page: row.page,
        text,
        score: 1,
        ranks: { facts: i + 1 },
      };
      return { ...hit, citation: citationFor(hit) };
    });
  } finally {
    db.close();
  }
}

/**
 * Honesty about coverage (#9): question words that appear nowhere in the
 * library become a leading line saying so, instead of weak hits quietly
 * implying the corpus covers them.
 */
function absentTerms(lib: Library, question: string): string[] {
  const terms = questionTerms(question);
  if (!terms.length) return [];
  const db = openDb(lib.dbPath, { readOnly: true });
  try {
    const stmt = db.prepare('SELECT 1 FROM chunks WHERE instr(lower(text), ?) > 0 LIMIT 1');
    return terms.filter((t) => stmt.get(t) === undefined);
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
  /** Only hits from this paper (#5's "ask this paper"). Turns the spine off — a paper lives in one library. */
  paper?: string;
  /** Filled in with what the graph walk started from. */
  trace?: QueryTrace;
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
  // Scoped to one paper, the rankings need to run deep enough that its
  // chunks surface at all before the filter keeps only them.
  const perList = options.paper ? 400 : Math.max(20, limit * 3);
  let hits: QueryHit[] = [];
  for (const l of libs) hits.push(...(await queryOne(l, question, embedder, perList, options.graph !== false, trace)));
  if (options.paper) hits = hits.filter((h) => h.paper === options.paper);
  hits = hits.sort((a, b) => b.score - a.score).slice(0, limit);

  // The facts lead (#9), and what the library lacks is said out loud — both
  // judged over every library searched, spine included: a term is missing
  // only when no searched library holds it.
  let facts: QueryHit[] = [];
  for (const l of libs) facts.push(...factsFor(l, question, 3));
  facts = facts.slice(0, 3);
  if (options.paper) facts = facts.filter((h) => h.paper === options.paper);
  const missing = libs
    .map((l) => new Set(absentTerms(l, question)))
    .reduce((acc, set) => new Set([...acc].filter((t) => set.has(t))));
  let lead: QueryHit[] = [];
  if (missing.size) {
    // Before saying "nothing", look across the hall: a sibling library in
    // the same root that covers the missing terms is the real answer to
    // "why are these results unpromising" — the question went to the wrong
    // shelf, and the line should say which shelf to ask.
    const searched = new Set(libs.map((l) => l.manifest.id));
    const elsewhere = listLibraries(lib.root)
      .filter((other) => !searched.has(other.manifest.id))
      .filter((other) => absentTerms(other, question).every((t) => !missing.has(t)))
      .map((other) => other.manifest.name)
      .slice(0, 2);
    const terms = [...missing].map((t) => `"${t}"`).join(' or ');
    lead = [{
      library: lib.manifest.id,
      chunk: -1,
      paper: '',
      title: lib.manifest.name,
      year: null,
      journal: null,
      doi: null,
      section: 'the library itself',
      kind: 'coverage',
      page: null,
      text: elsewhere.length
        ? `Nothing in the ${lib.manifest.name} library mentions ${terms} — but ${elsewhere.join(' and ')} does. Switch the library, or \`lit config ${lib.manifest.id} --include <its id>\` to share its spine.`
        : `Nothing in the ${lib.manifest.name} library mentions ${terms} — \`lit search\` can stage papers on it.`,
      score: 1,
      ranks: {},
      citation: `${lib.manifest.name} — coverage`,
    }];
  }
  const seen = new Set(facts.map((f) => f.text));
  return [...lead, ...facts, ...hits.filter((h) => !seen.has(h.text))];
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
