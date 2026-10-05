/**
 * The library as a graph: its papers and the candidates their citations found, joined by who
 * cites whom, laid out by a force simulation and drawn on a canvas — Obsidian's graph view, for a
 * literature. The simulation, the colour scales, the choice of labels and the hit-testing are pure
 * functions so the tests can hold them still; `GraphView` is the window around them. The worker's
 * `graph` op is the only source of what is drawn.
 */

import { el } from './shared.ts';

// ---- the contract: what the worker's `graph` op returns ------------------------------------------

export interface GraphNode {
  /** the work: a held paper's key ("doi:10.1/x", "pmid:123", "sha256:…") or "cand:<n>" for a candidate not held */
  id: string;
  /** the papers.key when held */
  paper: string | null;
  /** the candidate's id when it is one */
  cand_id: number | null;
  state: 'held' | 'candidate';
  /** a held paper's parse status ("parsed", …) or a candidate's ("found", "needs-pdf", "staged", …) */
  status: string;
  title: string | null;
  /** short: "Akkus 2017" */
  label: string;
  year: number | null;
  first_author: string | null;
  journal: string | null;
  /** 1 = found by a search or dropped in; 2 = found through the citations of round-1 papers; … */
  round: number;
  /** how many works in this graph cite it */
  cited_here: number;
  /** how many works in this graph it cites */
  cites_here: number;
  /** a held paper's kind ("research", "review", …) */
  type: string | null;
}

/** `src` cites `dst`. */
export interface GraphEdge {
  src: string;
  dst: string;
  origin: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type ColourBy = 'round' | 'year' | 'state';

export interface Point {
  x: number;
  y: number;
}

// ---- the simulation ----------------------------------------------------------------------------------

/** d3-force's constants, so the picture behaves like the graphs people already know. */
export const FORCE = {
  /** the many-body strength: negative, so every pair pushes apart */
  charge: -30,
  /** a spring's rest length between two discs' edges; both radii are added, so a hub does not sit on its neighbours */
  linkDistance: 30,
  /** the pull to the centre that keeps a work with no citations here from drifting off the canvas;
   *  it sets the mean spacing (√(π·30/0.02) ≈ 69), which must stay well above a spring's length
   *  or clusters that do not cite each other interleave */
  gravity: 0.02,
  /** what is left of a velocity after each tick (d3's velocityDecay of 0.4) */
  velocityDecay: 0.6,
  /** Barnes–Hut's opening angle: a cell further than its width / theta is one body */
  theta: 0.9,
  /** below this many nodes every pair is summed exactly; the tree costs more than it saves */
  exactBelow: 300,
  alphaMin: 0.001,
  /** cools from 1 to alphaMin in 300 ticks */
  alphaDecay: 1 - Math.pow(0.001, 1 / 300),
} as const;

/** mulberry32: a small seeded PRNG, so the same data draws the same picture every time. */
export function prng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A nudge too small to see, to part two nodes that sit exactly on each other (d3's jiggle). */
const jiggle = (rand: () => number) => (rand() - 0.5) * 1e-6;

const INITIAL_RADIUS = 10;
const INITIAL_ANGLE = Math.PI * (3 - Math.sqrt(5));

/** Where a fresh layout starts: d3's phyllotaxis spiral, the best-connected works nearest the
 *  middle (they end there anyway, and the cooling is shorter for it), turned by the seed and
 *  nudged a hair so no three start exactly in line. `from` skips the first slots, for works added
 *  to a picture that already fills them. */
export function initialPositions(nodes: readonly GraphNode[], seed = 1, from = 0): Point[] {
  const rand = prng(seed);
  const turn = rand() * 2 * Math.PI;
  const degree = (n: GraphNode) => (n.cited_here || 0) + (n.cites_here || 0);
  const order = nodes.map((_, i) => i).sort((a, b) => degree(nodes[b]!) - degree(nodes[a]!) || a - b);
  const out = new Array<Point>(nodes.length);
  order.forEach((i, slot) => {
    const s = slot + from;
    const radius = INITIAL_RADIUS * Math.sqrt(0.5 + s);
    const angle = s * INITIAL_ANGLE + turn;
    out[i] = { x: radius * Math.cos(angle) + (rand() - 0.5) * 1e-3, y: radius * Math.sin(angle) + (rand() - 0.5) * 1e-3 };
  });
  return out;
}

/** Who touches whom, either way round — the works a hover lights. An edge to a work not in the
 *  graph, or from a work to itself, joins nothing. */
export function adjacencyOf(data: GraphData): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>();
  for (const n of data.nodes) adj.set(n.id, new Set());
  for (const e of data.edges) {
    if (e.src === e.dst) continue;
    const a = adj.get(e.src);
    const b = adj.get(e.dst);
    if (!a || !b) continue;
    a.add(e.dst);
    b.add(e.src);
  }
  return adj;
}

/** A work and the works it cites or is cited by; nothing for a work not in the graph. */
export function neighbourhood(adj: ReadonlyMap<string, ReadonlySet<string>>, id: string): Set<string> {
  const near = adj.get(id);
  if (!near) return new Set();
  return new Set([id, ...near]);
}

/** A refresh's starting positions: a work already on the picture stays where it is, a new one
 *  starts beside the works it cites or is cited by (ring by ring, so a chain of new works follows
 *  its anchor), and one with no placed neighbour on the spiral's outer turns — so new rows grow
 *  the picture rather than scramble it. With nothing placed before, this is `initialPositions`. */
export function placeNodes(data: GraphData, prev: ReadonlyMap<string, Point>, seed = 1): Point[] {
  const { nodes } = data;
  const kept = (id: string) => {
    const p = prev.get(id);
    return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? p : undefined;
  };
  if (!nodes.some((n) => kept(n.id))) return initialPositions(nodes, seed);
  const rand = prng(seed ^ 0x9e3779b9);
  const adj = adjacencyOf(data);
  const at = new Map<string, Point>();
  const out: (Point | undefined)[] = nodes.map((n) => {
    const p = kept(n.id);
    if (!p) return undefined;
    at.set(n.id, { x: p.x, y: p.y });
    return { x: p.x, y: p.y };
  });
  let pending = nodes.map((_, i) => i).filter((i) => !out[i]);
  for (let ring = 0; pending.length && ring < 16; ring++) {
    const placed: number[] = [];
    const still: number[] = [];
    for (const i of pending) {
      let sx = 0;
      let sy = 0;
      let m = 0;
      for (const nb of adj.get(nodes[i]!.id) ?? []) {
        const p = at.get(nb);
        if (p) (sx += p.x), (sy += p.y), m++;
      }
      if (!m) {
        still.push(i);
        continue;
      }
      const a = rand() * 2 * Math.PI;
      const d = FORCE.linkDistance * (0.5 + 0.5 * rand());
      out[i] = { x: sx / m + d * Math.cos(a), y: sy / m + d * Math.sin(a) };
      placed.push(i);
    }
    if (!placed.length) break;
    for (const i of placed) at.set(nodes[i]!.id, out[i]!);
    pending = still;
  }
  if (pending.length) {
    const outer = initialPositions(pending.map((i) => nodes[i]!), seed, nodes.length - pending.length);
    pending.forEach((i, j) => (out[i] = outer[j]!));
  }
  return out as Point[];
}

/** A held paper's disc grows with how many works here cite it; a candidate's is smaller. */
export function nodeRadius(n: Pick<GraphNode, 'state' | 'cited_here'>): number {
  const base = n.state === 'held' ? 3.5 : 2.5;
  return Math.min(30, base * Math.sqrt(1 + Math.max(0, n.cited_here || 0)));
}

/** A Barnes–Hut quadtree in flat arrays, rebuilt each tick into the same memory: a cell is a leaf
 *  holding a chain of points, or the parent of up to four cells; each knows its mass (a count —
 *  every node repels alike) and centre of mass. */
export interface Quad {
  count: number;
  x0: Float64Array;
  y0: Float64Array;
  w: Float64Array;
  /** four per cell, -1 where there is none */
  child: Int32Array;
  /** a leaf's first point, -1 when empty or a parent */
  head: Int32Array;
  leaf: Uint8Array;
  mass: Float64Array;
  mx: Float64Array;
  my: Float64Array;
  /** per point: the next in its leaf's chain */
  next: Int32Array;
}

const MAX_DEPTH = 48;

function quadOf(cap: number, n: number, old?: Quad): Quad {
  const q: Quad = {
    count: 0,
    x0: new Float64Array(cap),
    y0: new Float64Array(cap),
    w: new Float64Array(cap),
    child: new Int32Array(cap * 4),
    head: new Int32Array(cap),
    leaf: new Uint8Array(cap),
    mass: new Float64Array(cap),
    mx: new Float64Array(cap),
    my: new Float64Array(cap),
    next: old && old.next.length >= n ? old.next : new Int32Array(Math.max(n, 1)),
  };
  if (old) {
    q.count = old.count;
    q.x0.set(old.x0);
    q.y0.set(old.y0);
    q.w.set(old.w);
    q.child.set(old.child);
    q.head.set(old.head);
    q.leaf.set(old.leaf);
  }
  return q;
}

export function buildQuadtree(x: ArrayLike<number>, y: ArrayLike<number>, n: number, reuse?: Quad): Quad {
  let q = reuse && reuse.next.length >= n ? reuse : quadOf(4 * n + 8, n);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    const xi = x[i]!, yi = y[i]!;
    if (!Number.isFinite(xi) || !Number.isFinite(yi)) continue;
    if (xi < minX) minX = xi;
    if (xi > maxX) maxX = xi;
    if (yi < minY) minY = yi;
    if (yi > maxY) maxY = yi;
  }
  const cell = (x0: number, y0: number, w: number): number => {
    if (q.count >= q.x0.length) q = quadOf(q.x0.length * 2, n, q);
    const c = q.count++;
    q.x0[c] = x0;
    q.y0[c] = y0;
    q.w[c] = w;
    q.leaf[c] = 1;
    q.head[c] = -1;
    q.child.fill(-1, 4 * c, 4 * c + 4);
    return c;
  };
  q.count = 0;
  if (minX > maxX) {
    cell(0, 0, 1);
    q.mass[0] = 0;
    return q;
  }
  const size = Math.max(maxX - minX, maxY - minY, 1e-9) * (1 + 1e-9) + 1e-9;
  cell(minX, minY, size);
  const quadrant = (c: number, px: number, py: number) => {
    const h = q.w[c]! / 2;
    return (px >= q.x0[c]! + h ? 1 : 0) | (py >= q.y0[c]! + h ? 2 : 0);
  };
  const childAt = (c: number, k: number) => {
    const h = q.w[c]! / 2;
    const d = cell(q.x0[c]! + (k & 1 ? h : 0), q.y0[c]! + (k & 2 ? h : 0), h);
    q.child[4 * c + k] = d;
    return d;
  };
  for (let i = 0; i < n; i++) {
    const xi = x[i]!, yi = y[i]!;
    if (!Number.isFinite(xi) || !Number.isFinite(yi)) continue;
    let c = 0;
    for (let depth = 0; ; depth++) {
      if (q.leaf[c]) {
        const p = q.head[c]!;
        if (p < 0) {
          q.head[c] = i;
          q.next[i] = -1;
          break;
        }
        if (depth >= MAX_DEPTH || (x[p] === xi && y[p] === yi)) {
          q.next[i] = p;
          q.head[c] = i;
          break;
        }
        // split: the chain here (all one position) goes down a level, then i follows it down
        q.leaf[c] = 0;
        q.head[c] = -1;
        const d = childAt(c, quadrant(c, x[p]!, y[p]!));
        q.head[d] = p;
      }
      const k = quadrant(c, xi, yi);
      const d = q.child[4 * c + k]!;
      if (d < 0) {
        q.head[childAt(c, k)] = i;
        q.next[i] = -1;
        break;
      }
      c = d;
    }
  }
  // children always come after their parent, so one pass backwards sums every cell
  for (let c = q.count - 1; c >= 0; c--) {
    let m = 0, sx = 0, sy = 0;
    if (q.leaf[c]) {
      for (let p = q.head[c]!; p >= 0; p = q.next[p]!) (m += 1), (sx += x[p]!), (sy += y[p]!);
    } else {
      for (let k = 0; k < 4; k++) {
        const d = q.child[4 * c + k]!;
        if (d < 0 || !q.mass[d]) continue;
        m += q.mass[d]!;
        sx += q.mx[d]! * q.mass[d]!;
        sy += q.my[d]! * q.mass[d]!;
      }
    }
    q.mass[c] = m;
    q.mx[c] = m ? sx / m : 0;
    q.my[c] = m ? sy / m : 0;
  }
  return q;
}

/** The simulation's state: the nodes in flat arrays, the edges as index pairs with d3's spring
 *  strength (weaker at a hub) and bias (the less-connected end moves more). */
export interface Sim {
  n: number;
  ids: string[];
  index: Map<string, number>;
  x: Float64Array;
  y: Float64Array;
  vx: Float64Array;
  vy: Float64Array;
  /** where a pinned node is held; NaN while it is free */
  fx: Float64Array;
  fy: Float64Array;
  r: Float64Array;
  src: Int32Array;
  dst: Int32Array;
  dist: Float64Array;
  strength: Float64Array;
  bias: Float64Array;
  rand: () => number;
  quad?: Quad;
}

export function createSim(data: GraphData, at: readonly Point[] = initialPositions(data.nodes), seed = 1): Sim {
  const n = data.nodes.length;
  const ids = data.nodes.map((d) => d.id);
  const index = new Map(ids.map((id, i) => [id, i]));
  const x = new Float64Array(n);
  const y = new Float64Array(n);
  const r = new Float64Array(n);
  data.nodes.forEach((d, i) => {
    x[i] = at[i]?.x ?? 0;
    y[i] = at[i]?.y ?? 0;
    r[i] = nodeRadius(d);
  });
  const pairs: [number, number][] = [];
  const seen = new Set<string>();
  for (const e of data.edges) {
    const a = index.get(e.src);
    const b = index.get(e.dst);
    if (a === undefined || b === undefined || a === b || seen.has(`${a}>${b}`)) continue;
    seen.add(`${a}>${b}`);
    pairs.push([a, b]);
  }
  const count = new Int32Array(n);
  for (const [a, b] of pairs) count[a]!++, count[b]!++;
  const m = pairs.length;
  const sim: Sim = {
    n, ids, index, x, y, r,
    vx: new Float64Array(n),
    vy: new Float64Array(n),
    fx: new Float64Array(n).fill(NaN),
    fy: new Float64Array(n).fill(NaN),
    src: new Int32Array(m),
    dst: new Int32Array(m),
    dist: new Float64Array(m),
    strength: new Float64Array(m),
    bias: new Float64Array(m),
    rand: prng(seed ^ 0x51ed27),
  };
  pairs.forEach(([a, b], k) => {
    sim.src[k] = a;
    sim.dst[k] = b;
    sim.dist[k] = FORCE.linkDistance + r[a]! + r[b]!;
    sim.strength[k] = 1 / Math.min(count[a]!, count[b]!);
    sim.bias[k] = count[a]! / (count[a]! + count[b]!);
  });
  return sim;
}

/** Springs along the edges, as d3's forceLink: each pulls its ends toward the rest length,
 *  moving the end with fewer edges more. */
export function linkForce(s: Sim, alpha: number): void {
  const { x, y, vx, vy, src, dst, dist, strength, bias } = s;
  for (let k = 0; k < src.length; k++) {
    const a = src[k]!, b = dst[k]!;
    let dx = x[b]! + vx[b]! - x[a]! - vx[a]! || jiggle(s.rand);
    let dy = y[b]! + vy[b]! - y[a]! - vy[a]! || jiggle(s.rand);
    let l = Math.sqrt(dx * dx + dy * dy);
    l = ((l - dist[k]!) / l) * alpha * strength[k]!;
    dx *= l;
    dy *= l;
    const w = bias[k]!;
    vx[b]! -= dx * w;
    vy[b]! -= dy * w;
    vx[a]! += dx * (1 - w);
    vy[a]! += dy * (1 - w);
  }
}

/** One pair's push, as d3's forceManyBody: inversely with distance, never from closer than 1. */
function push(s: Sim, i: number, j: number, k: number): void {
  let dx = s.x[j]! - s.x[i]!;
  let dy = s.y[j]! - s.y[i]!;
  let l = dx * dx + dy * dy;
  if (dx === 0) (dx = jiggle(s.rand)), (l += dx * dx);
  if (dy === 0) (dy = jiggle(s.rand)), (l += dy * dy);
  if (l < 1) l = Math.sqrt(l);
  s.vx[i]! += (dx * k) / l;
  s.vy[i]! += (dy * k) / l;
}

/** Every pair repels — exactly, which is O(n²); the yardstick Barnes–Hut is measured against. */
export function chargeExact(s: Sim, alpha: number, strength: number = FORCE.charge): void {
  const k = strength * alpha;
  for (let i = 0; i < s.n; i++) for (let j = 0; j < s.n; j++) if (j !== i) push(s, i, j, k);
}

/** Every pair repels, a far cell of the quadtree counted as one body at its centre of mass. A
 *  cell holding the node itself is always opened, so a node never pushes itself. */
export function chargeBarnesHut(s: Sim, alpha: number, strength: number = FORCE.charge, theta: number = FORCE.theta): void {
  const q = (s.quad = buildQuadtree(s.x, s.y, s.n, s.quad));
  const theta2 = theta * theta;
  const k = strength * alpha;
  const stack: number[] = [];
  for (let i = 0; i < s.n; i++) {
    const xi = s.x[i]!, yi = s.y[i]!;
    if (!Number.isFinite(xi) || !Number.isFinite(yi)) continue;
    stack.length = 0;
    stack.push(0);
    while (stack.length) {
      const c = stack.pop()!;
      const m = q.mass[c]!;
      if (!m) continue;
      if (q.leaf[c]) {
        for (let p = q.head[c]!; p >= 0; p = q.next[p]!) if (p !== i) push(s, i, p, k);
        continue;
      }
      const w = q.w[c]!, x0 = q.x0[c]!, y0 = q.y0[c]!;
      const inside = xi >= x0 && xi < x0 + w && yi >= y0 && yi < y0 + w;
      const dx = q.mx[c]! - xi, dy = q.my[c]! - yi;
      let l = dx * dx + dy * dy;
      if (!inside && (w * w) / theta2 < l) {
        if (l < 1) l = Math.sqrt(l);
        s.vx[i]! += (dx * k * m) / l;
        s.vy[i]! += (dy * k * m) / l;
        continue;
      }
      for (let c4 = 4 * c, j = 0; j < 4; j++) {
        const d = q.child[c4 + j]!;
        if (d >= 0) stack.push(d);
      }
    }
  }
}

/** One step, as d3-force takes it: springs, repulsion, the weak pull to the centre (which is
 *  what keeps a work nothing here cites in view), then each free node moves by its damped
 *  velocity and each pinned one stays where it was put. */
export function tick(s: Sim, alpha: number, opts: { theta?: number; exact?: boolean } = {}): void {
  linkForce(s, alpha);
  if (opts.exact ?? s.n < FORCE.exactBelow) chargeExact(s, alpha);
  else chargeBarnesHut(s, alpha, FORCE.charge, opts.theta);
  const g = FORCE.gravity * alpha;
  for (let i = 0; i < s.n; i++) {
    s.vx[i]! -= s.x[i]! * g;
    s.vy[i]! -= s.y[i]! * g;
    if (Number.isFinite(s.fx[i]!)) {
      s.x[i] = s.fx[i]!;
      s.y[i] = s.fy[i]!;
      s.vx[i] = 0;
      s.vy[i] = 0;
    } else {
      s.x[i]! += s.vx[i]! *= FORCE.velocityDecay;
      s.y[i]! += s.vy[i]! *= FORCE.velocityDecay;
    }
  }
}

/** d3's cooling: alpha eases toward its target, a fixed share of the way each tick. */
export function cool(alpha: number, target = 0): number {
  return alpha + (target - alpha) * FORCE.alphaDecay;
}

export function positionsOf(s: Sim): Map<string, Point> {
  return new Map(s.ids.map((id, i) => [id, { x: s.x[i]!, y: s.y[i]! }]));
}

/** A whole layout, start to cold: what the window shows after a few seconds, computed at once. */
export function simulate(
  data: GraphData,
  opts: { ticks?: number; seed?: number; theta?: number; exact?: boolean; from?: ReadonlyMap<string, Point> } = {},
): Map<string, Point> {
  const seed = opts.seed ?? 1;
  const s = createSim(data, opts.from ? placeNodes(data, opts.from, seed) : initialPositions(data.nodes, seed), seed);
  let alpha = 1;
  for (let t = 0; t < (opts.ticks ?? 300); t++) {
    alpha = cool(alpha);
    tick(s, alpha, opts);
  }
  return positionsOf(s);
}

// ---- colour ----------------------------------------------------------------------------------

/** The colours the canvas draws with, read from styles.css's graph tokens at draw time. */
export interface GraphPalette {
  bg: string;
  ink: string;
  muted: string;
  edge: string;
  /** rounds 1, 2 and 3, and every later round folded into one */
  round: [string, string, string, string];
  /** no value to colour by: a work with no year */
  none: string;
  /** oldest, middle, newest */
  year: [string, string, string];
  held: string;
  candidate: string;
  font: string;
}

/** What the canvas falls back on when styles.css's graph tokens are not there (the light steps). */
export const FALLBACK_PALETTE: GraphPalette = {
  bg: '#ffffff',
  ink: '#1f2328',
  muted: '#6b7280',
  edge: '#8a8f98',
  round: ['#2a78d6', '#eb6834', '#1baf7a', '#9aa3ad'],
  none: '#c9c4b8',
  year: ['#86b6ef', '#2a78d6', '#0d366b'],
  held: '#2a78d6',
  candidate: '#eb6834',
  font: '-apple-system, "Segoe UI", Inter, system-ui, sans-serif',
};

/** The palette from a token getter (`--graph-*`), each missing token its fallback. */
export function readPalette(token: (name: string) => string, font?: string): GraphPalette {
  const f = FALLBACK_PALETTE;
  const t = (name: string, dflt: string) => token(`--graph-${name}`).trim() || dflt;
  return {
    bg: t('bg', f.bg),
    ink: t('ink', f.ink),
    muted: t('muted', f.muted),
    edge: t('edge', f.edge),
    round: [t('r1', f.round[0]), t('r2', f.round[1]), t('r3', f.round[2]), t('r4', f.round[3])],
    none: t('none', f.none),
    year: [t('year-lo', f.year[0]), t('year-mid', f.year[1]), t('year-hi', f.year[2])],
    held: t('held', f.held),
    candidate: t('candidate', f.candidate),
    font: font?.trim() || f.font,
  };
}

/** `#rgb`, `#rrggbb` or `rgb(…)` as three bytes; null for anything else. */
export function parseColour(c: string): [number, number, number] | null {
  const s = c.trim();
  let m = /^#([0-9a-f]{3})$/i.exec(s);
  if (m) return [...m[1]!].map((h) => parseInt(h + h, 16)) as [number, number, number];
  m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(s);
  if (m) return [0, 2, 4].map((o) => parseInt(m![1]!.slice(o, o + 2), 16)) as [number, number, number];
  m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(s);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  return null;
}

const hex2 = (v: number) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0');

/** A point `t` (0–1) along a ramp of stops, mixed in sRGB between the two stops either side. */
export function ramp(stops: readonly string[], t: number): string {
  const rgb = stops.map(parseColour);
  if (!rgb.length || rgb.some((c) => !c)) return stops[0] ?? '#888888';
  const u = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0)) * (rgb.length - 1);
  const j = Math.min(rgb.length - 2, Math.floor(u));
  if (j < 0) return `#${rgb[0]!.map(hex2).join('')}`;
  const f = u - j;
  const a = rgb[j]!, b = rgb[j + 1]!;
  return `#${a.map((v, k) => hex2(v + (b[k]! - v) * f)).join('')}`;
}

/** Which round slot a work's colour comes from: 0, 1, 2 for rounds 1–3, 3 for every later one,
 *  -1 for a round that is not one. */
export function roundSlot(round: number): number {
  if (!Number.isInteger(round) || round < 1) return -1;
  return Math.min(round, 4) - 1;
}

/** The oldest and newest years in the graph; null when no work has one. */
export function yearExtent(nodes: readonly Pick<GraphNode, 'year'>[]): [number, number] | null {
  let lo = Infinity, hi = -Infinity;
  for (const n of nodes) {
    if (n.year === null || !Number.isFinite(n.year)) continue;
    if (n.year < lo) lo = n.year;
    if (n.year > hi) hi = n.year;
  }
  return lo <= hi ? [lo, hi] : null;
}

/** A work's colour under a mode. Year runs light-to-dark (in the light theme) from the oldest
 *  year present to the newest; a work with no year, or a round that is not one, is grey. */
export function nodeColour(n: GraphNode, mode: ColourBy, p: GraphPalette, years: [number, number] | null): string {
  if (mode === 'state') return n.state === 'held' ? p.held : p.candidate;
  if (mode === 'year') {
    if (n.year === null || !years) return p.none;
    return ramp(p.year, years[1] === years[0] ? 0.5 : (n.year - years[0]) / (years[1] - years[0]));
  }
  const slot = roundSlot(n.round);
  return slot < 0 ? p.none : p.round[slot as 0 | 1 | 2 | 3];
}

export interface LegendSwatch {
  label: string;
  colour: string;
  count: number;
  title?: string;
}

export interface Legend {
  swatches: LegendSwatch[];
  /** the year mode's ramp, oldest to newest */
  ramp?: { from: number; to: number; stops: string[] };
}

const ROUND_TITLES = ['found by a search or dropped in', 'found through the citations of round-1 papers', 'found through the citations of round-2 papers', 'found further out'];

/** The key for a mode: only the classes the graph has, each with how many works are in it. */
export function legendFor(nodes: readonly GraphNode[], mode: ColourBy, p: GraphPalette): Legend {
  if (mode === 'state') {
    const held = nodes.filter((n) => n.state === 'held').length;
    return {
      swatches: [
        { label: 'held', colour: p.held, count: held },
        { label: 'candidate', colour: p.candidate, count: nodes.length - held },
      ].filter((s) => s.count > 0),
    };
  }
  if (mode === 'year') {
    const years = yearExtent(nodes);
    const none = nodes.filter((n) => n.year === null).length;
    return {
      swatches: none ? [{ label: 'no year', colour: p.none, count: none }] : [],
      ...(years ? { ramp: { from: years[0], to: years[1], stops: [...p.year] } } : {}),
    };
  }
  const counts = [0, 0, 0, 0];
  let none = 0;
  for (const n of nodes) {
    const s = roundSlot(n.round);
    if (s < 0) none++;
    else counts[s]!++;
  }
  const swatches: LegendSwatch[] = counts.flatMap((count, s) =>
    count ? [{ label: s < 3 ? `round ${s + 1}` : 'round 4+', colour: p.round[s as 0 | 1 | 2 | 3], count, title: ROUND_TITLES[s]! }] : [],
  );
  if (none) swatches.push({ label: 'no round', colour: p.none, count: none });
  return { swatches };
}

// ---- view, hits, labels --------------------------------------------------------------------------------

/** The camera: a world point (x, y) is drawn at (x·k + tx, y·k + ty) in CSS pixels. */
export interface View {
  k: number;
  tx: number;
  ty: number;
}

export const ZOOM_MIN = 0.03;
export const ZOOM_MAX = 8;

/** Zoom by `factor` keeping the world point under (px, py) under it. */
export function zoomAround(v: View, px: number, py: number, factor: number): View {
  const k = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, v.k * factor));
  const f = k / v.k;
  return { k, tx: px - (px - v.tx) * f, ty: py - (py - v.ty) * f };
}

/** The view that shows every disc whole inside a w×h canvas with `pad` to spare, no closer than
 *  2× (a graph of three works should not fill the screen). */
export function fitView(s: Pick<Sim, 'n' | 'x' | 'y' | 'r'>, w: number, h: number, pad = 40): View {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < s.n; i++) {
    const x = s.x[i]!, y = s.y[i]!, r = s.r[i]!;
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    x0 = Math.min(x0, x - r);
    x1 = Math.max(x1, x + r);
    y0 = Math.min(y0, y - r);
    y1 = Math.max(y1, y + r);
  }
  if (x0 > x1) return { k: 1, tx: w / 2, ty: h / 2 };
  const k = Math.max(ZOOM_MIN, Math.min(2, (w - 2 * pad) / Math.max(x1 - x0, 1), (h - 2 * pad) / Math.max(y1 - y0, 1)));
  return { k, tx: w / 2 - ((x0 + x1) / 2) * k, ty: h / 2 - ((y0 + y1) / 2) * k };
}

/** Partway (t, 0–1) between two views of a w×h canvas: the centre glides, the zoom changes by
 *  equal ratios, so a flight neither lurches nor overshoots. */
export function interpolateView(a: View, b: View, t: number, w: number, h: number): View {
  const ca = { x: (w / 2 - a.tx) / a.k, y: (h / 2 - a.ty) / a.k };
  const cb = { x: (w / 2 - b.tx) / b.k, y: (h / 2 - b.ty) / b.k };
  const k = Math.exp(Math.log(a.k) + (Math.log(b.k) - Math.log(a.k)) * t);
  const cx = ca.x + (cb.x - ca.x) * t;
  const cy = ca.y + (cb.y - ca.y) * t;
  return { k, tx: w / 2 - cx * k, ty: h / 2 - cy * k };
}

/** The order the discs are drawn in: candidates under held papers, and within each the large
 *  under the small, so a small disc on a hub can still be seen and hit. */
export function drawOrder(nodes: readonly Pick<GraphNode, 'state'>[], r: ArrayLike<number>): number[] {
  return nodes
    .map((_, i) => i)
    .sort((a, b) => (nodes[a]!.state === 'held' ? 1 : 0) - (nodes[b]!.state === 'held' ? 1 : 0) || r[b]! - r[a]! || a - b);
}

/** The node under a world point: of the discs (widened by `slop`) that contain it, the one drawn
 *  last — the one on top. -1 when there is none. */
export function hitTest(order: readonly number[], x: ArrayLike<number>, y: ArrayLike<number>, r: ArrayLike<number>, px: number, py: number, slop = 0): number {
  for (let o = order.length - 1; o >= 0; o--) {
    const i = order[o]!;
    const dx = x[i]! - px, dy = y[i]! - py, rr = r[i]! + slop;
    if (dx * dx + dy * dy <= rr * rr) return i;
  }
  return -1;
}

/** The works with the most citations here: the ones labelled even when zoomed out. */
export function topByCited(nodes: readonly Pick<GraphNode, 'cited_here'>[], n: number): Set<number> {
  return new Set(
    nodes
      .map((d, i) => [d.cited_here || 0, i] as const)
      .filter(([c]) => c > 0)
      .sort((a, b) => b[0] - a[0] || a[1] - b[1])
      .slice(0, n)
      .map(([, i]) => i),
  );
}

/** A label that may be drawn, for the node centred at (x, y) with radius r, in screen pixels. */
export interface LabelCandidate {
  i: number;
  x: number;
  y: number;
  r: number;
  w: number;
  h: number;
  /** hovered, selected or highlighted: drawn even where there is no room */
  forced: boolean;
  /** may sit above, right or left of its node when the space below is taken */
  shift: boolean;
  /** the order labels claim space in, heaviest first, forced ones before all */
  weight: number;
}

/** A label placed: `x` is its centre, `y` its top. */
export interface PlacedLabel {
  i: number;
  x: number;
  y: number;
  w: number;
  h: number;
  forced: boolean;
}

/** The labels drawn, in the order they claim their space: the forced ones first, then the rest by
 *  weight. Each goes under its node, or (if it may shift) above, right or left of it, wherever it
 *  overlaps no label already placed; a forced one with nowhere free goes under its node anyway,
 *  and any other is left out — so zooming in adds labels and zooming out thins them, and they
 *  never become a carpet. */
export function chooseLabels(cands: readonly LabelCandidate[], gap = 2): PlacedLabel[] {
  const CELL = 64;
  const grid = new Map<number, PlacedLabel[]>();
  const keyOf = (cx: number, cy: number) => cx * 73856093 + cy * 19349663;
  const span = (c: PlacedLabel) => [
    Math.floor((c.x - c.w / 2 - gap) / CELL), Math.floor((c.y - gap) / CELL),
    Math.floor((c.x + c.w / 2 + gap) / CELL), Math.floor((c.y + c.h + gap) / CELL),
  ] as const;
  const overlaps = (a: PlacedLabel, b: PlacedLabel) =>
    Math.abs(a.x - b.x) * 2 < a.w + b.w + 2 * gap && a.y < b.y + b.h + gap && b.y < a.y + a.h + gap;
  const free = (c: PlacedLabel) => {
    const [gx0, gy0, gx1, gy1] = span(c);
    for (let gx = gx0; gx <= gx1; gx++) for (let gy = gy0; gy <= gy1; gy++) for (const o of grid.get(keyOf(gx, gy)) ?? []) if (overlaps(c, o)) return false;
    return true;
  };
  const placed: PlacedLabel[] = [];
  const claim = (c: PlacedLabel) => {
    const [gx0, gy0, gx1, gy1] = span(c);
    for (let gx = gx0; gx <= gx1; gx++)
      for (let gy = gy0; gy <= gy1; gy++) {
        const k = keyOf(gx, gy);
        const list = grid.get(k);
        if (list) list.push(c);
        else grid.set(k, [c]);
      }
    placed.push(c);
  };
  const spots = (c: LabelCandidate): PlacedLabel[] => {
    const at = (x: number, y: number): PlacedLabel => ({ i: c.i, x, y, w: c.w, h: c.h, forced: c.forced });
    const below = at(c.x, c.y + c.r + 3);
    if (!c.shift) return [below];
    const side = c.y - c.h / 2;
    return [below, at(c.x, c.y - c.r - 3 - c.h), at(c.x + c.r + 4 + c.w / 2, side), at(c.x - c.r - 4 - c.w / 2, side)];
  };
  const order = [...cands].sort((a, b) => Number(b.forced) - Number(a.forced) || b.weight - a.weight || a.i - b.i);
  for (const c of order) {
    const options = spots(c);
    const spot = options.find(free) ?? (c.forced ? options[0] : undefined);
    if (spot) claim(spot);
  }
  return placed;
}

/** The works whose label, title or first author holds the text, case-blind; none for no text. */
export function matchNodes(nodes: readonly GraphNode[], text: string): GraphNode[] {
  const q = text.trim().toLowerCase();
  if (!q) return [];
  return nodes.filter((n) => [n.label, n.title, n.first_author].some((s) => s?.toLowerCase().includes(q)));
}

/** A work as the tooltip tells it: its title, then who and when, where, what state, how cited. */
export function describeNode(n: GraphNode): { title: string; lines: string[] } {
  const join = (parts: (string | number | null | undefined)[]) => parts.filter((p) => p !== null && p !== undefined && p !== '').join(' · ');
  const round = roundSlot(n.round) >= 0 ? `round ${n.round}` : null;
  return {
    title: n.title?.trim() || n.label,
    lines: [
      join([n.first_author, n.year]),
      join([n.journal]),
      join([n.state, n.status, n.type, round]),
      join([`cited by ${n.cited_here} here`, `cites ${n.cites_here} here`]),
    ].filter(Boolean),
  };
}

// ---- the view -------------------------------------------------------------------------------------

export interface GraphViewOptions {
  onSelect?: (n: GraphNode | null) => void;
  onOpen?: (n: GraphNode) => void;
  colourBy?: ColourBy;
}

/** How hot a drag keeps the simulation: enough for neighbours to follow, not for the picture to boil. */
const DRAG_HEAT = 0.15;
/** Past this zoom every node may be labelled, space allowing; below it, only the most cited. */
const LABEL_ALL_ZOOM = 1.2;
const TOP_LABELS = 12;
/** A highlight bigger than this labels by space, not by force: a query that returns half the library should not bury the picture in text. */
const FORCED_HIGHLIGHT_MAX = 24;
/** Past this zoom an edge shows its direction with an arrowhead at the cited end. */
const ARROW_ZOOM = 0.9;
const LABEL_FONT_PX = 11;
const SEED = 1;

type Gesture =
  | { kind: 'node'; i: number; sx: number; sy: number; ox: number; oy: number; moved: boolean }
  | { kind: 'pan'; sx: number; sy: number; tx: number; ty: number; moved: boolean }
  | { kind: 'pinch'; d: number; mid: Point; view: View };

export class GraphView {
  private readonly root: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly g: CanvasRenderingContext2D;
  private readonly legendBox: HTMLElement;
  private readonly tip: HTMLElement;
  private readonly abort = new AbortController();
  private readonly resizer: ResizeObserver;
  private readonly onSelect?: (n: GraphNode | null) => void;
  private readonly onOpen?: (n: GraphNode) => void;
  private colourBy: ColourBy;

  private data: GraphData = { nodes: [], edges: [] };
  private sim: Sim = createSim({ nodes: [], edges: [] }, []);
  private adj = new Map<string, Set<string>>();
  private order: number[] = [];
  private top = new Set<number>();
  private colours: string[] = [];
  private labelWidths = new Float64Array(0);
  private palette: GraphPalette | null = null;

  private view: View = { k: 1, tx: 0, ty: 0 };
  private width = 0;
  private height = 0;
  private dpr = 1;
  private alpha = 0;
  private alphaTarget = 0;
  private autoFit = false;
  private flight: { from: View; to: View; t0: number; ms: number } | null = null;
  private pendingFocus: string | null = null;

  private hovered = -1;
  private selected = -1;
  private highlighted: Set<string> | null = null;
  /** per node, whether it is lit while something is hovered or highlighted; null when all are */
  private lit: Uint8Array | null = null;
  private readonly pinned = new Set<string>();

  private readonly pointers = new Map<number, Point>();
  private gesture: Gesture | null = null;
  private tipFor = -1;
  private raf = 0;
  private dirty = true;
  private destroyed = false;

  constructor(host: HTMLElement, opts: GraphViewOptions = {}) {
    this.onSelect = opts.onSelect;
    this.onOpen = opts.onOpen;
    this.colourBy = opts.colourBy ?? 'round';
    this.root = el('div', 'graphview');
    this.canvas = el('canvas', 'graphview-canvas') as HTMLCanvasElement;
    this.canvas.tabIndex = 0;
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', 'Citation graph of the library');
    this.legendBox = el('div', 'graphview-legend');
    this.tip = el('div', 'graphview-tip');
    this.tip.hidden = true;
    this.root.append(this.canvas, this.legendBox, this.tip);
    host.append(this.root);
    const g = this.canvas.getContext('2d');
    if (!g) throw new Error('graphview: this window cannot draw on a canvas');
    this.g = g;

    const on = { signal: this.abort.signal };
    const c = this.canvas;
    c.addEventListener('pointerdown', (e) => this.pointerDown(e), on);
    c.addEventListener('pointermove', (e) => this.pointerMove(e), on);
    c.addEventListener('pointerup', (e) => this.pointerUp(e, true), on);
    c.addEventListener('pointercancel', (e) => this.pointerUp(e, false), on);
    c.addEventListener('pointerleave', () => !this.gesture && this.setHover(-1), on);
    c.addEventListener('dblclick', (e) => this.doubleClick(e), on);
    c.addEventListener('wheel', (e) => this.wheel(e), { signal: this.abort.signal, passive: false });
    c.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.selected >= 0) this.select(-1, true);
    }, on);
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.retheme(), on);

    this.resizer = new ResizeObserver((entries) => {
      const r = entries[entries.length - 1]!.contentRect;
      this.resize(r.width, r.height);
    });
    this.resizer.observe(this.root);
    this.renderLegend();
  }

  // ---- the API ----------------------------------------------------------------------------

  /** Draw this graph. Works already on the picture keep their places (and pins), a new one starts
   *  beside a neighbour, and the simulation warms only as much as the change calls for. */
  setData(d: GraphData): void {
    const prev = positionsOf(this.sim);
    const selected = this.selected >= 0 ? this.sim.ids[this.selected] : undefined;
    const oldEdges = new Set(Array.from(this.sim.src, (a, k) => `${this.sim.ids[a]}>${this.sim.ids[this.sim.dst[k]!]}`));

    this.data = d;
    this.sim = createSim(d, placeNodes(d, prev, SEED), SEED);
    this.adj = adjacencyOf(d);
    this.order = drawOrder(d.nodes, this.sim.r);
    this.top = topByCited(d.nodes, TOP_LABELS);
    this.colours = [];
    this.labelWidths = new Float64Array(d.nodes.length);
    for (const id of [...this.pinned]) {
      const i = this.sim.index.get(id);
      if (i === undefined) this.pinned.delete(id);
      else (this.sim.fx[i] = this.sim.x[i]!), (this.sim.fy[i] = this.sim.y[i]!);
    }
    // the selection survives a refresh; the hover is the pointer's, and the next move finds it again
    this.selected = selected === undefined ? -1 : (this.sim.index.get(selected) ?? -1);
    this.hovered = -1;
    this.hideTip();

    const kept = d.nodes.filter((n) => prev.has(n.id)).length;
    const edges = Array.from(this.sim.src, (a, k) => `${this.sim.ids[a]}>${this.sim.ids[this.sim.dst[k]!]}`);
    const changed = kept < d.nodes.length || kept < prev.size || edges.length !== oldEdges.size || edges.some((e) => !oldEdges.has(e));
    if (!kept) {
      this.alpha = 1;
      this.autoFit = true;
    } else if (changed) this.alpha = Math.max(this.alpha, 0.3);
    this.relight();
    this.renderLegend();
    this.redraw();
  }

  setColourBy(mode: ColourBy): void {
    if (mode === this.colourBy) return;
    this.colourBy = mode;
    this.colours = [];
    this.renderLegend();
    this.redraw();
  }

  /** Light these works and dim the rest (the works a query returned, say); null lights all. */
  highlight(ids: Set<string> | null): void {
    this.highlighted = ids;
    this.relight();
    this.redraw();
  }

  /** The works whose label, title or first author holds the text, lit; empty text clears it. */
  find(text: string): GraphNode[] {
    const found = matchNodes(this.data.nodes, text);
    this.highlight(text.trim() ? new Set(found.map((n) => n.id)) : null);
    return found;
  }

  /** Glide to one work and select it. A choice made by the caller, so `onSelect` is not called back. */
  focus(id: string): void {
    const i = this.sim.index.get(id);
    if (i === undefined) return;
    this.select(i, false);
    if (!this.width || !this.height) {
      this.pendingFocus = id; // the middle of a hidden canvas is nowhere: glide once it is shown
      return;
    }
    const k = Math.min(ZOOM_MAX, Math.max(this.view.k, 1.5));
    this.fly({ k, tx: this.width / 2 - this.sim.x[i]! * k, ty: this.height / 2 - this.sim.y[i]! * k });
  }

  /** Zoom to show every work; while hidden, as soon as it is shown. */
  fit(): void {
    if (!this.width || !this.height) {
      this.autoFit = true;
      return;
    }
    this.fly(fitView(this.sim, this.width, this.height));
  }

  destroy(): void {
    this.destroyed = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.resizer.disconnect();
    this.abort.abort();
    this.root.remove();
  }

  // ---- the frame loop: only while something moves -----------------------------------------------

  private redraw(): void {
    this.dirty = true;
    this.wake();
  }

  private wake(): void {
    if (this.raf || this.destroyed) return;
    this.raf = requestAnimationFrame((now) => this.frame(now));
  }

  private frame(now: number): void {
    this.raf = 0;
    if (!this.width || !this.height) return; // hidden: the ResizeObserver wakes it when shown
    const hot = this.alpha >= FORCE.alphaMin || this.alphaTarget > 0;
    if (hot) {
      this.alpha = cool(this.alpha, this.alphaTarget);
      tick(this.sim, this.alpha);
      if (this.autoFit) this.view = fitView(this.sim, this.width, this.height);
      this.dirty = true;
    }
    if (this.flight) {
      const f = this.flight;
      const t = Math.min(1, (now - f.t0) / f.ms);
      const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      this.view = interpolateView(f.from, f.to, e, this.width, this.height);
      if (t >= 1) this.flight = null;
      this.dirty = true;
    }
    if (this.dirty) this.draw();
    if (hot || this.flight) this.wake();
  }

  private fly(to: View, ms = 450): void {
    this.autoFit = false;
    this.flight = { from: { ...this.view }, to, t0: performance.now(), ms };
    this.wake();
  }

  private resize(w: number, h: number): void {
    if (this.width && this.height && w && h) {
      // keep the middle of the picture in the middle
      this.view = { ...this.view, tx: this.view.tx + (w - this.width) / 2, ty: this.view.ty + (h - this.height) / 2 };
    } else if (w && h && !this.width && this.view.tx === 0 && this.view.ty === 0) {
      this.view = { ...this.view, tx: w / 2, ty: h / 2 };
    }
    this.width = w;
    this.height = h;
    this.backing();
    if (w && h && this.autoFit && this.alpha < FORCE.alphaMin) {
      this.view = fitView(this.sim, w, h);
      this.autoFit = false;
    }
    if (w && h && this.pendingFocus !== null) {
      const id = this.pendingFocus;
      this.pendingFocus = null;
      this.focus(id);
    }
    this.redraw();
  }

  /** The canvas's pixels: its CSS size times the device's pixel ratio, rechecked each draw since a
   *  window dragged to another screen changes the ratio without resizing anything. */
  private backing(): void {
    this.dpr = window.devicePixelRatio || 1;
    const pw = Math.max(1, Math.round(this.width * this.dpr));
    const ph = Math.max(1, Math.round(this.height * this.dpr));
    if (this.canvas.width !== pw) this.canvas.width = pw;
    if (this.canvas.height !== ph) this.canvas.height = ph;
  }

  private retheme(): void {
    this.palette = null;
    this.colours = [];
    this.renderLegend();
    this.redraw();
  }

  /** The theme's colours, read once per theme — but not kept while the view is not yet in the
   *  document, where every token reads empty and the fallbacks would stick. */
  private paletteNow(): GraphPalette {
    if (this.palette) return this.palette;
    const cs = getComputedStyle(this.root);
    const p = readPalette((n) => cs.getPropertyValue(n), cs.fontFamily);
    if (this.root.isConnected) this.palette = p;
    return p;
  }

  // ---- drawing -----------------------------------------------------------------------------------

  private draw(): void {
    this.dirty = false;
    if ((window.devicePixelRatio || 1) !== this.dpr) this.backing();
    const { g, sim, width: w, height: h, lit } = this;
    const { k, tx, ty } = this.view;
    const p = this.paletteNow();
    if (this.colours.length !== sim.n) {
      const years = yearExtent(this.data.nodes);
      this.colours = this.data.nodes.map((n) => nodeColour(n, this.colourBy, p, years));
    }
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.globalAlpha = 1;
    g.fillStyle = p.bg;
    g.fillRect(0, 0, w, h);

    const { x, y, r, src, dst } = sim;
    const sx = (i: number) => x[i]! * k + tx;
    const sy = (i: number) => y[i]! * k + ty;
    const margin = 40;
    const onScreen = (i: number, pad: number) => {
      const X = sx(i), Y = sy(i);
      return X > -pad && X < w + pad && Y > -pad && Y < h + pad;
    };
    const edgeLit = (a: number, b: number) =>
      this.hovered >= 0 ? a === this.hovered || b === this.hovered : lit ? !!(lit[a] && lit[b]) : true;

    // edges: the quiet ones first, then the lit ones over them
    const arrows = k >= ARROW_ZOOM;
    const edges = (pass: 'quiet' | 'lit') => {
      const lines = new Path2D();
      const heads = new Path2D();
      for (let e = 0; e < src.length; e++) {
        const a = src[e]!, b = dst[e]!;
        if ((pass === 'lit') !== (lit !== null && edgeLit(a, b))) continue;
        const x1 = sx(a), y1 = sy(a), x2 = sx(b), y2 = sy(b);
        if ((x1 < 0 && x2 < 0) || (y1 < 0 && y2 < 0) || (x1 > w && x2 > w) || (y1 > h && y2 > h)) continue;
        const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
        const rb = Math.max(1.5, r[b]! * k);
        lines.moveTo(x1, y1);
        if (arrows && len > rb + 14) {
          const ux = dx / len, uy = dy / len;
          const tipX = x2 - ux * (rb + 1.5), tipY = y2 - uy * (rb + 1.5);
          const bx = tipX - ux * 6, by = tipY - uy * 6;
          lines.lineTo(bx, by);
          heads.moveTo(tipX, tipY);
          heads.lineTo(bx - uy * 2.6, by + ux * 2.6);
          heads.lineTo(bx + uy * 2.6, by - ux * 2.6);
          heads.closePath();
        } else lines.lineTo(x2, y2);
      }
      return { lines, heads };
    };
    g.lineWidth = 1;
    {
      const { lines, heads } = edges('quiet');
      g.globalAlpha = lit ? 0.07 : 0.3;
      g.strokeStyle = p.edge;
      g.fillStyle = p.edge;
      g.stroke(lines);
      if (arrows) g.fill(heads);
    }
    if (lit) {
      const { lines, heads } = edges('lit');
      g.globalAlpha = 0.65;
      g.strokeStyle = p.ink;
      g.fillStyle = p.ink;
      g.lineWidth = 1.2;
      g.stroke(lines);
      if (arrows) g.fill(heads);
    }

    // discs, in draw order; the dimmed ones faint, the hovered one again on top
    const disc = (i: number) => {
      const R = Math.max(1.5, r[i]! * k);
      const X = sx(i), Y = sy(i);
      const colour = this.colours[i]!;
      g.beginPath();
      g.arc(X, Y, R, 0, 2 * Math.PI);
      if (this.data.nodes[i]!.state === 'held') {
        g.fillStyle = colour;
        g.fill();
        if (R > 3) {
          g.lineWidth = 1;
          g.strokeStyle = p.bg;
          g.stroke();
        }
      } else {
        g.fillStyle = p.bg;
        g.fill();
        g.lineWidth = Math.max(1, Math.min(2, R * 0.4));
        g.strokeStyle = colour;
        g.stroke();
      }
      if (this.pinned.has(sim.ids[i]!)) {
        g.beginPath();
        g.arc(X, Y, R + 2.5, 0, 2 * Math.PI);
        g.setLineDash([2, 2]);
        g.lineWidth = 1;
        g.strokeStyle = p.muted;
        g.stroke();
        g.setLineDash([]);
      }
      if (i === this.selected) {
        g.beginPath();
        g.arc(X, Y, R + 3.5, 0, 2 * Math.PI);
        g.lineWidth = 2;
        g.strokeStyle = p.ink;
        g.stroke();
      }
    };
    for (const i of this.order) {
      if (!onScreen(i, r[i]! * k + 4)) continue;
      g.globalAlpha = lit && !lit[i] ? 0.14 : 1;
      disc(i);
    }
    g.globalAlpha = 1;
    if (this.hovered >= 0) disc(this.hovered);
    if (this.selected >= 0 && this.selected !== this.hovered) disc(this.selected);

    this.drawLabels(p, onScreen, margin);
  }

  private drawLabels(p: GraphPalette, onScreen: (i: number, pad: number) => boolean, margin: number): void {
    const { g, sim, lit } = this;
    const { k, tx, ty } = this.view;
    const nodes = this.data.nodes;
    g.font = `${LABEL_FONT_PX}px ${p.font}`;
    const width = (i: number) => {
      if (!this.labelWidths[i]) this.labelWidths[i] = g.measureText(labelText(nodes[i]!)).width || 1;
      return this.labelWidths[i]!;
    };
    const hl = this.highlighted;
    const forceHl = !!hl && hl.size <= FORCED_HIGHLIGHT_MAX && this.hovered < 0;
    const near = this.hovered >= 0 ? this.lit : null;
    const everyone = k >= LABEL_ALL_ZOOM;
    const cands: LabelCandidate[] = [];
    for (let i = 0; i < sim.n; i++) {
      const forced = i === this.hovered || i === this.selected || (forceHl && hl.has(sim.ids[i]!));
      const dimmed = lit !== null && !lit[i];
      if (!forced && (dimmed || !onScreen(i, margin))) continue;
      const neighbour = !!near?.[i];
      const lighted = !!lit?.[i];
      if (!forced && !everyone && !neighbour && !this.top.has(i) && !(hl && lighted)) continue;
      cands.push({
        i,
        x: sim.x[i]! * k + tx,
        y: sim.y[i]! * k + ty,
        r: Math.max(1.5, sim.r[i]! * k),
        w: width(i),
        h: LABEL_FONT_PX + 2,
        forced,
        shift: forced || lighted,
        weight: (i === this.hovered || i === this.selected ? 1e7 : 0) + (neighbour ? 1e6 : 0) + (lighted ? 1e5 : 0) + (nodes[i]!.cited_here || 0) * 10 + sim.r[i]!,
      });
    }
    const chosen = chooseLabels(cands);
    g.textAlign = 'center';
    g.textBaseline = 'top';
    g.lineJoin = 'round';
    g.lineWidth = 3;
    g.strokeStyle = p.bg;
    for (const c of chosen) {
      const strong = c.i === this.hovered || c.i === this.selected;
      g.font = `${strong ? '600 ' : ''}${LABEL_FONT_PX}px ${p.font}`;
      g.fillStyle = strong || c.forced || lit?.[c.i] ? p.ink : p.muted;
      const text = labelText(nodes[c.i]!);
      g.strokeText(text, c.x, c.y);
      g.fillText(text, c.x, c.y);
    }
  }

  private renderLegend(): void {
    const lg = legendFor(this.data.nodes, this.colourBy, this.paletteNow());
    const box = this.legendBox;
    box.replaceChildren();
    const key = el('div', 'gv-key');
    if (lg.ramp) {
      const span = el('span', 'gv-ramp-row');
      const bar = el('i', 'gv-ramp');
      bar.style.background = `linear-gradient(90deg, ${lg.ramp.stops.join(', ')})`;
      span.append(el('span', undefined, String(lg.ramp.from)), bar, el('span', undefined, String(lg.ramp.to)));
      key.append(span);
    }
    for (const s of lg.swatches) {
      const item = el('span', 'gv-item');
      const sw = el('i', 'gv-sw');
      sw.style.background = s.colour;
      item.append(sw, el('span', undefined, s.label), el('b', undefined, String(s.count)));
      if (s.title) item.title = s.title;
      key.append(item);
    }
    const shapes = el('div', 'gv-key gv-shapes');
    const held = el('span', 'gv-item');
    held.append(el('i', 'gv-disc'), el('span', undefined, 'held'));
    const cand = el('span', 'gv-item');
    cand.append(el('i', 'gv-ring'), el('span', undefined, 'candidate'));
    shapes.append(held, cand);
    box.append(key, shapes);
    box.hidden = !this.data.nodes.length;
  }

  // ---- hover, selection, light -------------------------------------------------------------------

  private relight(): void {
    const n = this.sim.n;
    let ids: Set<string> | null = null;
    if (this.hovered >= 0) ids = neighbourhood(this.adj, this.sim.ids[this.hovered]!);
    else if (this.highlighted) ids = this.highlighted;
    if (!ids) {
      this.lit = null;
      return;
    }
    const lit = new Uint8Array(n);
    for (const id of ids) {
      const i = this.sim.index.get(id);
      if (i !== undefined) lit[i] = 1;
    }
    this.lit = lit;
  }

  private setHover(i: number, at?: Point): void {
    if (i !== this.hovered) {
      this.hovered = i;
      this.relight();
      this.redraw();
      this.canvas.style.cursor = i >= 0 ? 'pointer' : '';
    }
    if (i < 0) this.hideTip();
    else if (at) this.showTip(i, at);
  }

  private select(i: number, notify: boolean): void {
    if (i !== this.selected) {
      this.selected = i;
      this.redraw();
    }
    if (notify) this.onSelect?.(i >= 0 ? this.data.nodes[i]! : null);
  }

  private showTip(i: number, at: Point): void {
    const tip = this.tip;
    if (this.tipFor !== i) {
      this.tipFor = i;
      const node = this.data.nodes[i]!;
      const d = describeNode(node);
      tip.replaceChildren(el('div', 'gv-tip-title', d.title), ...d.lines.map((l) => el('div', 'gv-tip-meta', l)));
      if (this.pinned.has(node.id)) tip.append(el('div', 'gv-tip-hint', 'pinned — double-click to let it go'));
      tip.hidden = false;
    }
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const left = at.x + 14 + tw > this.width ? Math.max(4, at.x - 14 - tw) : at.x + 14;
    const top = at.y + 14 + th > this.height ? Math.max(4, at.y - 14 - th) : at.y + 14;
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  private hideTip(): void {
    this.tipFor = -1;
    this.tip.hidden = true;
  }

  // ---- pointer, wheel, keys ---------------------------------------------------------------------------

  private local(e: MouseEvent): Point {
    const b = this.canvas.getBoundingClientRect();
    return { x: e.clientX - b.left, y: e.clientY - b.top };
  }

  private world(p: Point): Point {
    return { x: (p.x - this.view.tx) / this.view.k, y: (p.y - this.view.ty) / this.view.k };
  }

  private hitAt(p: Point): number {
    const w = this.world(p);
    return hitTest(this.order, this.sim.x, this.sim.y, this.sim.r, w.x, w.y, 3 / this.view.k);
  }

  private pinch(): { d: number; mid: Point } {
    const [a, b] = [...this.pointers.values()];
    return { d: Math.max(1, Math.hypot(a!.x - b!.x, a!.y - b!.y)), mid: { x: (a!.x + b!.x) / 2, y: (a!.y + b!.y) / 2 } };
  }

  private pointerDown(e: PointerEvent): void {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    this.canvas.focus({ preventScroll: true });
    const p = this.local(e);
    this.pointers.set(e.pointerId, p);
    this.canvas.setPointerCapture(e.pointerId);
    this.flight = null;
    if (this.pointers.size === 2) {
      this.endNodeDrag();
      this.gesture = { kind: 'pinch', ...this.pinch(), view: { ...this.view } };
      return;
    }
    if (this.pointers.size > 2) return;
    const i = this.hitAt(p);
    if (i >= 0) {
      const w = this.world(p);
      this.gesture = { kind: 'node', i, sx: p.x, sy: p.y, ox: this.sim.x[i]! - w.x, oy: this.sim.y[i]! - w.y, moved: false };
    } else {
      this.gesture = { kind: 'pan', sx: p.x, sy: p.y, tx: this.view.tx, ty: this.view.ty, moved: false };
      this.canvas.style.cursor = 'grabbing';
    }
  }

  private pointerMove(e: PointerEvent): void {
    const p = this.local(e);
    if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, p);
    const gs = this.gesture;
    if (!gs) {
      this.setHover(this.hitAt(p), p);
      return;
    }
    if (gs.kind === 'pinch') {
      if (this.pointers.size < 2) return;
      const now = this.pinch();
      const panned = { ...gs.view, tx: gs.view.tx + now.mid.x - gs.mid.x, ty: gs.view.ty + now.mid.y - gs.mid.y };
      this.view = zoomAround(panned, now.mid.x, now.mid.y, now.d / gs.d);
      this.autoFit = false;
      this.redraw();
      return;
    }
    if (!gs.moved && Math.hypot(p.x - gs.sx, p.y - gs.sy) < 3) return;
    if (!gs.moved) {
      gs.moved = true;
      this.autoFit = false;
      this.hideTip();
      if (gs.kind === 'node') this.alphaTarget = DRAG_HEAT;
    }
    if (gs.kind === 'node') {
      const w = this.world(p);
      this.sim.fx[gs.i] = this.sim.x[gs.i] = w.x + gs.ox;
      this.sim.fy[gs.i] = this.sim.y[gs.i] = w.y + gs.oy;
    } else {
      this.view = { ...this.view, tx: gs.tx + p.x - gs.sx, ty: gs.ty + p.y - gs.sy };
    }
    this.redraw();
  }

  private pointerUp(e: PointerEvent, click: boolean): void {
    this.pointers.delete(e.pointerId);
    const gs = this.gesture;
    if (gs?.kind === 'pinch') {
      if (this.pointers.size < 2) this.gesture = null;
      return;
    }
    this.gesture = null;
    this.canvas.style.cursor = this.hovered >= 0 ? 'pointer' : '';
    if (!gs) return;
    if (gs.kind === 'node') {
      if (gs.moved) this.endNodeDrag(gs.i);
      else if (click) this.select(gs.i, true);
    } else if (!gs.moved && click) this.select(-1, true);
  }

  /** A dragged node stays where it was dropped, pinned, and the simulation cools again. */
  private endNodeDrag(i?: number): void {
    const gs = this.gesture;
    const node = i ?? (gs?.kind === 'node' && gs.moved ? gs.i : -1);
    if (node >= 0) this.pinned.add(this.sim.ids[node]!);
    this.alphaTarget = 0;
    this.wake();
  }

  /** On a pinned work, a double-click lets it go back to the simulation; on any other, opens it. */
  private doubleClick(e: MouseEvent): void {
    const i = this.hitAt(this.local(e));
    if (i < 0) return;
    const id = this.sim.ids[i]!;
    if (this.pinned.delete(id)) {
      this.sim.fx[i] = NaN;
      this.sim.fy[i] = NaN;
      this.alpha = Math.max(this.alpha, 0.1);
      this.hideTip();
      this.redraw();
      return;
    }
    this.onOpen?.(this.data.nodes[i]!);
  }

  private wheel(e: WheelEvent): void {
    e.preventDefault();
    const p = this.local(e);
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * this.height : e.deltaY;
    // a pinch on a trackpad arrives as a wheel with ctrl held, in much smaller steps
    this.view = zoomAround(this.view, p.x, p.y, Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015)));
    this.autoFit = false;
    this.flight = null;
    this.redraw();
  }
}

/** A label as drawn: the worker's short label, never longer than a line should be. */
function labelText(n: GraphNode): string {
  const s = n.label || n.title || n.id;
  return s.length > 40 ? `${s.slice(0, 39)}…` : s;
}
