import { describe, expect, it } from 'vitest';
import {
  FALLBACK_PALETTE, adjacencyOf, buildQuadtree, chargeBarnesHut, chargeExact, chooseLabels, createSim, describeNode, drawOrder,
  fitView, hitTest, initialPositions, interpolateView, legendFor, matchNodes, neighbourhood, nodeColour, nodeRadius, parseColour,
  placeNodes, prng, ramp, readPalette, roundSlot, simulate, topByCited, yearExtent, zoomAround,
  type GraphData, type GraphNode, type LabelCandidate, type Point,
} from '../src/renderer/graphview';

const node = (id: string, over: Partial<GraphNode> = {}): GraphNode => ({
  id, paper: id, cand_id: null, state: 'held', status: 'parsed', title: `On ${id}`, label: id, year: 2020,
  first_author: 'Akkus', journal: null, round: 1, cited_here: 0, cites_here: 0, type: 'research', ...over,
});
const edge = (src: string, dst: string) => ({ src, dst, origin: 'references' });
const dist = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);

/** Two triangles that never cite each other, and a work nothing here cites. */
const islands: GraphData = {
  nodes: ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id) => node(id)),
  edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'a'), edge('d', 'e'), edge('e', 'f'), edge('f', 'd')],
};

/** A library of n works, each citing a few earlier ones, the early ones most. */
function library(n: number, seed = 7): GraphData {
  const rand = prng(seed);
  const nodes = Array.from({ length: n }, (_, i) => node(`w${i}`, { round: 1 + Math.floor(rand() * 3), year: 1990 + Math.floor(rand() * 35) }));
  const edges = [];
  for (let i = 1; i < n; i++) for (let j = 0; j < 1 + Math.floor(rand() * 3); j++) edges.push(edge(`w${i}`, `w${Math.floor(rand() * rand() * i)}`));
  return { nodes, edges };
}

describe('the layout', () => {
  it('starts on a seeded spiral: finite, the same for a seed, different for another, the most connected in the middle', () => {
    const nodes = [node('x'), node('hub', { cited_here: 9 }), node('y'), node('z')];
    const a = initialPositions(nodes, 3);
    expect(a.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
    expect(initialPositions(nodes, 3)).toEqual(a);
    expect(initialPositions(nodes, 4)).not.toEqual(a);
    const r = a.map((p) => Math.hypot(p.x, p.y));
    expect(Math.min(...r)).toBe(r[1]);
  });

  it('settles to finite positions, the same every time for a seed', () => {
    const one = simulate(islands, { ticks: 300, seed: 5 });
    expect([...one.values()].every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
    expect(simulate(islands, { ticks: 300, seed: 5 })).toEqual(one);
    expect(simulate(islands, { ticks: 300, seed: 6 })).not.toEqual(one);
  });

  it('draws works that cite each other closer than works that do not, and keeps a lone work in view', () => {
    const at = simulate(islands, { ticks: 300, seed: 1 });
    const p = (id: string) => at.get(id)!;
    const within = [dist(p('a'), p('b')), dist(p('b'), p('c')), dist(p('c'), p('a')), dist(p('d'), p('e')), dist(p('e'), p('f')), dist(p('f'), p('d'))];
    const across = ['a', 'b', 'c'].flatMap((u) => ['d', 'e', 'f'].map((v) => dist(p(u), p(v))));
    expect(Math.max(...within)).toBeLessThan(Math.min(...across));
    // the centre's pull holds the work no one cites: near the others, not flung off
    const lone = p('g');
    expect(Math.hypot(lone.x, lone.y)).toBeLessThan(300);
    expect(Math.min(...['a', 'b', 'c', 'd', 'e', 'f'].map((v) => dist(lone, p(v))))).toBeGreaterThan(20);
  });

  it('a Barnes–Hut step pushes as the exact sum does, within a few percent; with theta 0 it is the exact sum', () => {
    const data = library(160);
    const rand = prng(11);
    const at = data.nodes.map(() => ({ x: (rand() - 0.5) * 600, y: (rand() - 0.5) * 400 }));
    const step = (how: (s: ReturnType<typeof createSim>) => void) => {
      const s = createSim(data, at);
      how(s);
      return { vx: Array.from(s.vx), vy: Array.from(s.vy) };
    };
    const exact = step((s) => chargeExact(s, 0.5));
    const approx = step((s) => chargeBarnesHut(s, 0.5, -30, 0.9));
    const err = (a: typeof exact) => {
      let num = 0, den = 0;
      for (let i = 0; i < a.vx.length; i++) {
        num += (a.vx[i]! - exact.vx[i]!) ** 2 + (a.vy[i]! - exact.vy[i]!) ** 2;
        den += exact.vx[i]! ** 2 + exact.vy[i]! ** 2;
      }
      return Math.sqrt(num / den);
    };
    expect(err(approx)).toBeLessThan(0.05);
    expect(err(step((s) => chargeBarnesHut(s, 0.5, -30, 0)))).toBeLessThan(1e-9);
  });

  it('builds a quadtree whose root holds every point at their centre of mass, coincident points included', () => {
    const x = [0, 10, 10, 10, -4];
    const y = [0, 10, 10, 10, 6];
    const q = buildQuadtree(x, y, 5);
    expect(q.mass[0]).toBe(5);
    expect(q.mx[0]).toBeCloseTo(26 / 5);
    expect(q.my[0]).toBeCloseTo(36 / 5);
    const big = library(3000);
    const s = createSim(big, initialPositions(big.nodes, 2));
    chargeBarnesHut(s, 1);
    expect(Array.from(s.vx).every(Number.isFinite)).toBe(true);
  });

  it('on a refresh keeps every surviving work where it was, and starts a new one beside its neighbour', () => {
    const before = simulate(islands, { ticks: 120 });
    const grown: GraphData = {
      nodes: [...islands.nodes.filter((n) => n.id !== 'g'), node('h'), node('loner')],
      edges: [...islands.edges, edge('h', 'a')],
    };
    const at = placeNodes(grown, before, 1);
    grown.nodes.forEach((n, i) => {
      if (before.has(n.id)) expect(at[i]).toEqual(before.get(n.id));
    });
    const h = at[grown.nodes.findIndex((n) => n.id === 'h')]!;
    expect(dist(h, before.get('a')!)).toBeLessThanOrEqual(30 + 1e-9);
    const loner = at[grown.nodes.findIndex((n) => n.id === 'loner')]!;
    expect(Number.isFinite(loner.x) && Number.isFinite(loner.y)).toBe(true);
    expect(placeNodes(islands, new Map(), 4)).toEqual(initialPositions(islands.nodes, 4));
  });

  it('grows a held paper’s disc with its citations here, a candidate’s smaller', () => {
    expect(nodeRadius({ state: 'held', cited_here: 0 })).toBeCloseTo(3.5);
    expect(nodeRadius({ state: 'held', cited_here: 8 })).toBeCloseTo(10.5);
    expect(nodeRadius({ state: 'candidate', cited_here: 8 })).toBeLessThan(nodeRadius({ state: 'held', cited_here: 8 }));
  });
});

describe('colour', () => {
  const p = FALLBACK_PALETTE;

  it('runs the year ramp from the oldest year present to the newest, a work with no year grey', () => {
    const nodes = [node('old', { year: 1998 }), node('mid', { year: 2011 }), node('new', { year: 2024 }), node('undated', { year: null })];
    const years = yearExtent(nodes);
    expect(years).toEqual([1998, 2024]);
    expect(nodeColour(nodes[0]!, 'year', p, years)).toBe(p.year[0]);
    expect(nodeColour(nodes[1]!, 'year', p, years)).toBe(p.year[1]);
    expect(nodeColour(nodes[2]!, 'year', p, years)).toBe(p.year[2]);
    expect(nodeColour(nodes[3]!, 'year', p, years)).toBe(p.none);
    expect(nodeColour(node('only'), 'year', p, [2020, 2020])).toBe(p.year[1]);
    expect(yearExtent([node('u', { year: null })])).toBeNull();
  });

  it('mixes a ramp between its stops', () => {
    expect(ramp(['#000000', '#ffffff'], 0)).toBe('#000000');
    expect(ramp(['#000000', '#ffffff'], 1)).toBe('#ffffff');
    expect(ramp(['#000000', '#ffffff'], 0.5)).toBe('#808080');
    expect(ramp(['#000', '#ff0000', '#fff'], 0.25)).toBe('#800000');
    expect(parseColour('rgb(1, 2, 3)')).toEqual([1, 2, 3]);
    expect(parseColour('papayawhip')).toBeNull();
  });

  it('gives rounds 1–3 their own colour, folds later rounds into one, and greys a round that is not one', () => {
    expect([1, 2, 3, 4, 9, 0, -1, 1.5].map(roundSlot)).toEqual([0, 1, 2, 3, 3, -1, -1, -1]);
    expect(nodeColour(node('x', { round: 2 }), 'round', p, null)).toBe(p.round[1]);
    expect(nodeColour(node('x', { round: 0 }), 'round', p, null)).toBe(p.none);
    expect(nodeColour(node('x', { state: 'candidate' }), 'state', p, null)).toBe(p.candidate);
  });

  it('keys only the classes the graph has, with their counts', () => {
    const nodes = [node('a'), node('b', { round: 2 }), node('c', { round: 5, state: 'candidate', year: null })];
    expect(legendFor(nodes, 'round', p).swatches.map((s) => [s.label, s.count])).toEqual([['round 1', 1], ['round 2', 1], ['round 4+', 1]]);
    expect(legendFor(nodes, 'state', p).swatches.map((s) => [s.label, s.count])).toEqual([['held', 2], ['candidate', 1]]);
    const years = legendFor(nodes, 'year', p);
    expect(years.ramp).toEqual({ from: 2020, to: 2020, stops: [...p.year] });
    expect(years.swatches.map((s) => s.label)).toEqual(['no year']);
  });

  it('reads the theme’s tokens, falling back where one is missing', () => {
    const tokens: Record<string, string> = { '--graph-bg': ' #1a1a19', '--graph-r1': '#3987e5' };
    const read = readPalette((n) => tokens[n] ?? '', 'Inter');
    expect(read.bg).toBe('#1a1a19');
    expect(read.round).toEqual(['#3987e5', ...FALLBACK_PALETTE.round.slice(1)]);
    expect(read.font).toBe('Inter');
    expect(readPalette(() => '').ink).toBe(FALLBACK_PALETTE.ink);
  });
});

describe('pointing, lighting and labelling', () => {
  it('hits the topmost disc under a point, and nothing between discs', () => {
    const nodes = [node('cand', { state: 'candidate' }), node('big'), node('small')];
    const x = [0, 0, 4], y = [0, 0, 0], r = [5, 10, 3];
    const order = drawOrder(nodes, r);
    expect(order).toEqual([0, 1, 2]); // candidates under held papers; the large under the small
    expect(hitTest(order, x, y, r, 5, 0)).toBe(2);
    expect(hitTest(order, x, y, r, -2, 0)).toBe(1);
    expect(hitTest(order, x, y, r, 30, 0)).toBe(-1);
    expect(hitTest(order, x, y, r, 12, 0, 3)).toBe(1); // within the slop
  });

  it('lights a work with what it cites and what cites it, either way round', () => {
    const adj = adjacencyOf({ nodes: [node('a'), node('b'), node('c'), node('d')], edges: [edge('a', 'b'), edge('c', 'a'), edge('a', 'zz'), edge('d', 'd')] });
    expect(neighbourhood(adj, 'a')).toEqual(new Set(['a', 'b', 'c']));
    expect(neighbourhood(adj, 'd')).toEqual(new Set(['d']));
    expect(neighbourhood(adj, 'nope')).toEqual(new Set());
  });

  it('places forced labels even without room and the rest only where there is room, heaviest first', () => {
    const at = (i: number, x: number, over: Partial<LabelCandidate> = {}): LabelCandidate => ({ i, x, y: 0, r: 4, w: 40, h: 12, forced: false, shift: false, weight: 0, ...over });
    const chosen = chooseLabels([
      at(0, 0, { weight: 1 }),
      at(1, 10, { weight: 5 }),
      at(2, 100, { weight: 0 }),
      at(3, 20, { forced: true }),
      at(4, 25, { forced: true }),
    ]);
    expect(chosen.map((c) => c.i)).toEqual([3, 4, 2]);
    expect(chosen[0]).toEqual({ i: 3, x: 20, y: 7, w: 40, h: 12, forced: true }); // under its node: centre + radius + 3
    // a crowd of a thousand labels in one spot is one label, not a carpet
    const crowd = Array.from({ length: 1000 }, (_, i) => at(i, (i % 10) * 3, { y: Math.floor(i / 10) * 0.1, weight: i }));
    expect(chooseLabels(crowd).length).toBe(1);
  });

  it('moves a label that may shift above, beside or left of its node before giving up on it', () => {
    const at = (i: number, over: Partial<LabelCandidate> = {}): LabelCandidate => ({ i, x: 0, y: 0, r: 8, w: 40, h: 12, forced: false, shift: true, weight: 10 - i, ...over });
    const four = chooseLabels([at(0), at(1), at(2), at(3), at(4)]);
    expect(four.map((c) => [c.i, c.x, c.y])).toEqual([[0, 0, 11], [1, 0, -23], [2, 32, -6], [3, -32, -6]]);
    const overlap = (a: (typeof four)[number], b: (typeof four)[number]) => Math.abs(a.x - b.x) * 2 < a.w + b.w && a.y < b.y + b.h && b.y < a.y + a.h;
    for (const a of four) for (const b of four) if (a !== b) expect(overlap(a, b)).toBe(false);
    // a forced one with every side taken still shows, under its node
    expect(chooseLabels([at(0), at(1), at(2), at(3), at(4, { forced: true, weight: 0 })]).map((c) => c.i)).toEqual([4, 0, 1, 2]);
    expect(topByCited([{ cited_here: 3 }, { cited_here: 0 }, { cited_here: 9 }, { cited_here: 3 }], 2)).toEqual(new Set([2, 0]));
  });

  it('finds works by label, title or first author, case-blind', () => {
    const nodes = [node('Akkus 2017', { first_author: 'Akkus' }), node('Chen 2020', { title: 'Tendon REPAIR in rats', first_author: 'Chen' }), node('Li 2001', { first_author: 'Li', title: null })];
    expect(matchNodes(nodes, 'repair').map((n) => n.id)).toEqual(['Chen 2020']);
    expect(matchNodes(nodes, 'AKK').map((n) => n.id)).toEqual(['Akkus 2017']);
    expect(matchNodes(nodes, '  ')).toEqual([]);
  });

  it('tells a work in the tooltip: title, who and when, where, state, citations', () => {
    const d = describeNode(node('Akkus 2017', { title: 'Collagen scaffolds', year: 2017, journal: 'Biomaterials', state: 'candidate', status: 'needs-pdf', type: null, round: 2, cited_here: 4, cites_here: 1 }));
    expect(d).toEqual({ title: 'Collagen scaffolds', lines: ['Akkus · 2017', 'Biomaterials', 'candidate · needs-pdf · round 2', 'cited by 4 here · cites 1 here'] });
    expect(describeNode(node('Li 2001', { title: null, first_author: null, year: null })).title).toBe('Li 2001');
  });
});

describe('the camera', () => {
  it('zooms around the pointer, keeping the world point under it', () => {
    const v = { k: 1, tx: 100, ty: 50 };
    const z = zoomAround(v, 300, 200, 2);
    const before = { x: (300 - v.tx) / v.k, y: (200 - v.ty) / v.k };
    expect(z.k).toBe(2);
    expect((300 - z.tx) / z.k).toBeCloseTo(before.x);
    expect((200 - z.ty) / z.k).toBeCloseTo(before.y);
    expect(zoomAround(v, 0, 0, 1e9).k).toBe(8);
  });

  it('fits every disc inside the canvas, and glides between views', () => {
    const s = createSim(islands, [[-500, 0], [500, 0], [0, -200], [0, 200], [10, 10], [20, 20], [30, 30]].map(([x, y]) => ({ x: x!, y: y! })));
    const v = fitView(s, 800, 600, 40);
    for (let i = 0; i < s.n; i++) {
      const X = s.x[i]! * v.k + v.tx, Y = s.y[i]! * v.k + v.ty, R = s.r[i]! * v.k;
      expect(X - R).toBeGreaterThanOrEqual(40 - 1e-6);
      expect(X + R).toBeLessThanOrEqual(760 + 1e-6);
      expect(Y - R).toBeGreaterThanOrEqual(-1e-6);
      expect(Y + R).toBeLessThanOrEqual(600 + 1e-6);
    }
    const a = { k: 1, tx: 0, ty: 0 }, b = { k: 4, tx: -100, ty: 30 };
    expect(interpolateView(a, b, 0, 800, 600)).toEqual(a);
    const end = interpolateView(a, b, 1, 800, 600);
    expect(end.k).toBeCloseTo(4);
    expect(end.tx).toBeCloseTo(-100);
    expect(interpolateView(a, b, 0.5, 800, 600).k).toBeCloseTo(2);
  });
});
