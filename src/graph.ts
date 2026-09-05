/**
 * The graph layer: what the store already knows, walked as a graph.
 *
 * After HippoRAG 2 (Gutiérrez et al., ICML 2025): entity nodes and passage
 * nodes, edges where a passage names an entity, personalized PageRank from
 * the question's entities and its nearest passages, passages ranked by where
 * the walk settles. Ours adds paper nodes and citation edges, because the
 * store has them. The entities come free from Europe PMC's text-mined terms
 * for open-access papers, and from the model stage's materials and methods.
 *
 * Nothing here is a second store: the graph is built from the tables at
 * query time, in memory, in milliseconds at this size.
 */

import type { DatabaseSync } from 'node:sqlite';

export interface Graph {
  /** node id → index */
  index: Map<string, number>;
  ids: string[];
  /** adjacency: for each node, [neighbour index, weight][] */
  adj: [number, number][][];
  /** chunk node index → chunk id */
  chunkOf: Map<number, number>;
  /** entity norm → node index */
  entityByNorm: Map<string, number>;
  entityNames: Map<number, string>;
  /** entity node index → how many chunks name it; specificity is its inverse. */
  entitySpread: Map<number, number>;
}

/** Entities mentioned in more than this share of the papers say nothing about any one of them. */
const HUB_SHARE = 0.6;

export function buildGraph(db: DatabaseSync): Graph {
  const index = new Map<string, number>();
  const ids: string[] = [];
  const adj: [number, number][][] = [];
  const node = (id: string): number => {
    let i = index.get(id);
    if (i === undefined) {
      i = ids.length;
      index.set(id, i);
      ids.push(id);
      adj.push([]);
    }
    return i;
  };
  const edge = (a: number, b: number, w: number) => {
    adj[a]!.push([b, w]);
    adj[b]!.push([a, w]);
  };

  const chunkOf = new Map<number, number>();
  const papers = db.prepare("SELECT key FROM papers WHERE status = 'ingested'").all() as { key: string }[];
  const paperCount = Math.max(1, papers.length);
  for (const p of papers) node(`paper:${p.key}`);

  for (const c of db.prepare("SELECT c.id, c.paper FROM chunks c JOIN papers p ON p.key = c.paper WHERE p.status = 'ingested'").all() as { id: number; paper: string }[]) {
    const ci = node(`chunk:${c.id}`);
    chunkOf.set(ci, c.id);
    edge(ci, node(`paper:${c.paper}`), 1);
  }

  // Citations: a paper to the papers it cites that the library holds.
  for (const r of db.prepare('SELECT DISTINCT paper, matched_paper FROM refs WHERE matched_paper IS NOT NULL').all() as { paper: string; matched_paper: string }[]) {
    if (index.has(`paper:${r.paper}`) && index.has(`paper:${r.matched_paper}`)) edge(node(`paper:${r.paper}`), node(`paper:${r.matched_paper}`), 1);
  }

  // Entities, minus the hubs: "collagen" in a collagen library links everything to everything.
  const entityByNorm = new Map<string, number>();
  const entityNames = new Map<number, string>();
  const entitySpread = new Map<number, number>();
  const spread = db.prepare('SELECT entity, COUNT(DISTINCT paper) n FROM mentions GROUP BY entity').all() as { entity: number; n: number }[];
  const hubs = new Set(spread.filter((s) => s.n / paperCount > HUB_SHARE && paperCount > 3).map((s) => s.entity));
  for (const e of db.prepare('SELECT id, name, norm FROM entities').all() as { id: number; name: string; norm: string }[]) {
    if (hubs.has(e.id)) continue;
    const ei = node(`entity:${e.id}`);
    entityByNorm.set(e.norm, ei);
    entityNames.set(ei, e.name);
  }
  for (const m of db.prepare('SELECT entity, paper, chunk, SUM(count) c FROM mentions GROUP BY entity, paper, chunk').all() as { entity: number; paper: string; chunk: number | null; c: number }[]) {
    if (hubs.has(m.entity)) continue;
    const ei = index.get(`entity:${m.entity}`);
    if (ei === undefined) continue;
    const target = m.chunk !== null ? index.get(`chunk:${m.chunk}`) : index.get(`paper:${m.paper}`);
    if (target === undefined) continue;
    edge(ei, target, Math.min(3, m.c));
    entitySpread.set(ei, (entitySpread.get(ei) ?? 0) + 1);
  }
  return { index, ids, adj, chunkOf, entityByNorm, entityNames, entitySpread };
}

/**
 * Entity nodes the question names, by whole-word match on the entity's name,
 * each weighted by its specificity — one over the passages that name it, as
 * HippoRAG weights its query nodes — so "genipin" leads and "cell" follows.
 */
export function entitySeeds(graph: Graph, question: string): Map<number, number> {
  const q = ` ${question.toLowerCase().replace(/[^a-z0-9µ%/.-]+/g, ' ')} `;
  const out = new Map<number, number>();
  for (const [norm, i] of graph.entityByNorm) {
    if (norm.length < 3) continue;
    if (q.includes(` ${norm} `) || q.includes(` ${norm}s `)) out.set(i, 1 / Math.max(1, graph.entitySpread.get(i) ?? 1));
  }
  return out;
}

/**
 * Personalized PageRank: the walk restarts at the seeds, damping 0.5 as
 * HippoRAG uses, thirty rounds — enough to settle on a graph this size.
 */
export function personalizedPageRank(graph: Graph, seeds: Map<number, number>, damping = 0.5, rounds = 30): Float64Array {
  const n = graph.ids.length;
  const p = new Float64Array(n);
  let total = 0;
  for (const w of seeds.values()) total += w;
  if (!total) return p;
  for (const [i, w] of seeds) p[i] = w / total;
  const degree = graph.adj.map((edges) => edges.reduce((s, [, w]) => s + w, 0));
  let r = Float64Array.from(p);
  for (let round = 0; round < rounds; round++) {
    const next = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const mass = r[i]!;
      if (!mass || !degree[i]) continue;
      for (const [j, w] of graph.adj[i]!) next[j]! += damping * mass * (w / degree[i]!);
    }
    for (let i = 0; i < n; i++) next[i]! += (1 - damping) * p[i]!;
    r = next;
  }
  return r;
}

function normalizeName(name: string): string {
  const n = name.toLowerCase().replace(/\s+/g, ' ').trim();
  return n.length > 4 && n.endsWith('s') && !n.endsWith('ss') ? n.slice(0, -1) : n;
}

export interface GraphHit {
  chunk: number;
  score: number;
}

/**
 * Chunks by where the walk settles, seeded from the question's entities
 * (weight 1 each) and its nearest passages (weight 0.05 each — HippoRAG 2's
 * passage-node weight, so entities lead and passages follow).
 */
export function graphSearch(graph: Graph, question: string, denseSeeds: number[], limit: number): { hits: GraphHit[]; seeds: string[] } {
  const seeds = new Map<number, number>();
  const named: string[] = [];
  const entities = entitySeeds(graph, question);
  // Passages weigh 0.05 against the strongest entity's 1, whatever the scale.
  const strongest = Math.max(...entities.values(), 0);
  for (const [i, w] of entities) {
    seeds.set(i, w / (strongest || 1));
    named.push(graph.entityNames.get(i) ?? graph.ids[i]!);
  }
  named.sort((a, b) => (entities.get(graph.entityByNorm.get(normalizeName(b))!) ?? 0) - (entities.get(graph.entityByNorm.get(normalizeName(a))!) ?? 0));
  for (const chunk of denseSeeds) {
    const i = graph.index.get(`chunk:${chunk}`);
    if (i !== undefined) seeds.set(i, (seeds.get(i) ?? 0) + 0.05);
  }
  if (!seeds.size) return { hits: [], seeds: [] };
  const rank = personalizedPageRank(graph, seeds);
  const hits: GraphHit[] = [];
  for (const [i, chunk] of graph.chunkOf) {
    const score = rank[i]!;
    if (score > 0) hits.push({ chunk, score });
  }
  hits.sort((a, b) => b.score - a.score);
  return { hits: hits.slice(0, limit), seeds: named };
}

export interface GraphStats {
  papers: number;
  chunks: number;
  entities: number;
  hubsDropped: number;
  citationEdges: number;
  mentionEdges: number;
  topEntities: { name: string; kind: string; papers: number }[];
}

export function graphStats(db: DatabaseSync, graph: Graph): GraphStats {
  let citationEdges = 0;
  let mentionEdges = 0;
  graph.ids.forEach((id, i) => {
    for (const [j] of graph.adj[i]!) {
      if (j < i) continue;
      const other = graph.ids[j]!;
      if (id.startsWith('paper:') && other.startsWith('paper:')) citationEdges += 1;
      if (id.startsWith('entity:') || other.startsWith('entity:')) mentionEdges += 1;
    }
  });
  const entities = (db.prepare('SELECT COUNT(*) n FROM entities').get() as { n: number }).n;
  const top = db
    .prepare('SELECT e.name, e.kind, COUNT(DISTINCT m.paper) papers FROM entities e JOIN mentions m ON m.entity = e.id GROUP BY e.id ORDER BY papers DESC, e.name LIMIT 15')
    .all() as GraphStats['topEntities'];
  return {
    papers: graph.ids.filter((id) => id.startsWith('paper:')).length,
    chunks: graph.chunkOf.size,
    entities: graph.entityByNorm.size,
    hubsDropped: entities - graph.entityByNorm.size,
    citationEdges,
    mentionEdges,
    topEntities: top,
  };
}

/**
 * The graph as data, for the app's Research tab (projtracker #48, filed here
 * as issue #3): entity and paper nodes, aggregated mention edges, citation
 * edges. Chunk nodes stay internal — thousands of them would drown a screen
 * that people navigate — and hubs are exported *marked* rather than dropped,
 * so a UI can dim "collagen" instead of mysteriously lacking it.
 */
export interface GraphExport {
  nodes: (
    | { id: string; type: 'paper'; key: string; title: string; year: number | null }
    | { id: string; type: 'entity'; name: string; kind: string; papers: number; hub: boolean }
  )[];
  edges: { from: string; to: string; type: 'mention' | 'citation'; weight: number }[];
  /** The share of papers past which an entity counts as a hub. */
  hubShare: number;
}

export function graphExport(db: DatabaseSync): GraphExport {
  const papers = db
    .prepare("SELECT key, title, year FROM papers WHERE status = 'ingested'")
    .all() as { key: string; title: string; year: number | null }[];
  const paperCount = Math.max(1, papers.length);
  const held = new Set(papers.map((p) => p.key));

  const nodes: GraphExport['nodes'] = papers.map((p) => ({
    id: `paper:${p.key}`,
    type: 'paper' as const,
    key: p.key,
    title: p.title,
    year: p.year,
  }));

  const spread = new Map(
    (db.prepare('SELECT entity, COUNT(DISTINCT paper) n FROM mentions GROUP BY entity').all() as {
      entity: number;
      n: number;
    }[]).map((s) => [s.entity, s.n] as const),
  );
  for (const e of db.prepare('SELECT id, name, kind FROM entities').all() as { id: number; name: string; kind: string }[]) {
    const papersOf = spread.get(e.id) ?? 0;
    if (!papersOf) continue;
    nodes.push({
      id: `entity:${e.id}`,
      type: 'entity',
      name: e.name,
      kind: e.kind,
      papers: papersOf,
      hub: papersOf / paperCount > HUB_SHARE && paperCount > 3,
    });
  }

  const edges: GraphExport['edges'] = [];
  for (const m of db
    .prepare('SELECT entity, paper, SUM(count) c FROM mentions GROUP BY entity, paper')
    .all() as { entity: number; paper: string; c: number }[]) {
    if (!held.has(m.paper)) continue;
    edges.push({ from: `entity:${m.entity}`, to: `paper:${m.paper}`, type: 'mention', weight: m.c });
  }
  for (const r of db
    .prepare('SELECT DISTINCT paper, matched_paper FROM refs WHERE matched_paper IS NOT NULL')
    .all() as { paper: string; matched_paper: string }[]) {
    if (!held.has(r.paper) || !held.has(r.matched_paper)) continue;
    edges.push({ from: `paper:${r.paper}`, to: `paper:${r.matched_paper}`, type: 'citation', weight: 1 });
  }
  return { nodes, edges, hubShare: HUB_SHARE };
}
