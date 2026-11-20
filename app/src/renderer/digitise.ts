/**
 * The digitiser's arithmetic, as the window needs it while a person works: the calibration
 * (pixels to values, mirroring parser/litrag_parser/digitise.py's `Calibration`, so a point's
 * value shows as it is dragged), the edits to a series' points, and the fit drawn as SVG — the
 * points, the curve and the residuals under them. The worker stays the authority: a saved
 * point's value is read again from its pixel there. Pure, so it can be tested.
 */

export interface AxisCal {
  p1: [number, number];
  v1: number;
  p2: [number, number];
  v2: number;
  log: boolean;
}

export interface CalibrationDict {
  x: AxisCal;
  y: AxisCal;
}

export interface Pt {
  px: number;
  py: number;
  how: 'auto' | 'added' | 'moved';
  x?: number;
  y?: number;
}

const tr = (v: number, log: boolean): number => {
  if (!log) return v;
  if (!(v > 0)) throw new Error(`a log axis has no value ${v}: its calibration values must be above zero`);
  return Math.log10(v);
};

function solve3(a: number[][], b: number[]): number[] | null {
  // Cramer's rule: three equations, three unknowns
  const det = (m: number[][]) =>
    m[0]![0]! * (m[1]![1]! * m[2]![2]! - m[1]![2]! * m[2]![1]!) - m[0]![1]! * (m[1]![0]! * m[2]![2]! - m[1]![2]! * m[2]![0]!) + m[0]![2]! * (m[1]![0]! * m[2]![1]! - m[1]![1]! * m[2]![0]!);
  const d = det(a);
  if (Math.abs(d) < 1e-9) return null;
  return [0, 1, 2].map((i) => det(a.map((row, r) => row.map((v, c) => (c === i ? b[r]! : v)))) / d);
}

/** Pixels to values: the x axis's two points share a y value, the y axis's an x value. */
export class Calibration {
  readonly m: number[][];
  constructor(readonly c: CalibrationDict) {
    const { x, y } = c;
    if (x.v1 === x.v2 || y.v1 === y.v2) throw new Error('each axis needs two different values');
    if (Math.hypot(x.p1[0] - x.p2[0], x.p1[1] - x.p2[1]) < 1 || Math.hypot(y.p1[0] - y.p2[0], y.p1[1] - y.p2[1]) < 1) throw new Error('each axis needs two points apart');
    const [X1, X2, Y1, Y2] = [x.p1, x.p2, y.p1, y.p2];
    const rx = solve3([[X1[0], X1[1], 1], [X2[0], X2[1], 1], [Y1[0] - Y2[0], Y1[1] - Y2[1], 0]], [tr(x.v1, x.log), tr(x.v2, x.log), 0]);
    const ry = solve3([[Y1[0], Y1[1], 1], [Y2[0], Y2[1], 1], [X1[0] - X2[0], X1[1] - X2[1], 0]], [tr(y.v1, y.log), tr(y.v2, y.log), 0]);
    if (!rx || !ry) throw new Error('the calibration points are in line: the x axis’s two points and the y axis’s two must run in different directions');
    this.m = [rx, ry];
  }

  value(px: number, py: number): [number, number] {
    const u = this.m[0]![0]! * px + this.m[0]![1]! * py + this.m[0]![2]!;
    const v = this.m[1]![0]! * px + this.m[1]![1]! * py + this.m[1]![2]!;
    return [this.c.x.log ? 10 ** u : u, this.c.y.log ? 10 ** v : v];
  }

}

/** A calibration from what a person has set, or why it does not hold yet. */
export function tryCalibration(c: Partial<{ x: Partial<AxisCal>; y: Partial<AxisCal> }>): { cal: Calibration | null; why: string | null } {
  const need: string[] = [];
  for (const a of ['x', 'y'] as const) {
    const ax = c[a] ?? {};
    if (!ax.p1) need.push(`${a.toUpperCase()}1’s point`);
    if (!ax.p2) need.push(`${a.toUpperCase()}2’s point`);
    if (ax.v1 === undefined || !Number.isFinite(ax.v1)) need.push(`${a.toUpperCase()}1’s value`);
    if (ax.v2 === undefined || !Number.isFinite(ax.v2)) need.push(`${a.toUpperCase()}2’s value`);
  }
  if (need.length) return { cal: null, why: `set ${need.join(', ')}` };
  try {
    return { cal: new Calibration(c as CalibrationDict), why: null };
  } catch (e) {
    return { cal: null, why: (e as Error).message };
  }
}

/** Every point's value through the calibration, in order of x, as the worker keeps them. */
export function valued(points: Pt[], cal: Calibration | null): Pt[] {
  const out = points.map((p) => {
    if (!cal) return { ...p, x: undefined, y: undefined };
    const [x, y] = cal.value(p.px, p.py);
    return { ...p, x, y };
  });
  if (cal) out.sort((a, b) => a.x! - b.x! || a.y! - b.y!);
  return out;
}

/** What of a saved series belongs on the picture shown. Its points and its calibration are pixels
 *  of the image it was read from (`image.sha256`): on that image, both; on any other — the figure
 *  cut differently since, or from another PDF — neither, so nothing of it is drawn there and no
 *  axes are taken as confirmed. Its saved values are not touched either way. */
export function onPicture(s: { calibration: CalibrationDict; points: Pt[]; image?: { sha256?: string } }, shown: string | undefined): { same: boolean; points: Pt[]; calibration: CalibrationDict | null } {
  const same = shown !== undefined && s.image?.sha256 === shown;
  return same ? { same, points: s.points.map((p) => ({ px: p.px, py: p.py, how: p.how })), calibration: { x: { ...s.calibration.x }, y: { ...s.calibration.y } } } : { same, points: [], calibration: null };
}

/** The (x, y) a fit is drawn over: the values of the points on the picture. With none on it, the
 *  values a kept series was saved with when it was read from another picture than the one
 *  `shown` — it has no point here to draw, and its fit is still of those values. A series of this
 *  picture with no points on it has had them cleared: nothing is drawn. */
export function charted(points: Pt[], kept: { calibration: CalibrationDict; points: Pt[]; image?: { sha256?: string } } | undefined, shown: string | undefined): [number, number][] {
  const from = points.length || !kept || onPicture(kept, shown).same ? points : kept.points;
  return from.filter((p) => p.x !== undefined && p.y !== undefined).map((p) => [p.x!, p.y!]);
}

/** An axis's heading — of a table's column here, of the CSV's in the worker (`_head` there, the
 *  same rule): its name and, in parentheses, its unit. A name that ends in a parenthetical loses
 *  it only when a unit is given to stand there instead ("Time (min)" with unit "min" is not
 *  "Time (min) (min)"); with no unit the name is kept whole, the parenthetical being all there is
 *  of one. */
export function axisHeading(label: string, unit: string, fallback: string): string {
  if (!unit) return label || fallback;
  return `${label.replace(/\s*\([^()]*\)\s*$/, '').trim() || fallback} (${unit})`;
}

/** What the answer to "save over the saved series?" saves: over it (0: the series of that id,
 *  under the name given), beside it (1: a new series, under the next default name), or nothing
 *  (anything else). */
export function saveOver(answer: number, saved: { id: number; series: string }[], active: number, name: string): { id: number | null; name: string } | null {
  if (answer === 0) return { id: active, name };
  if (answer === 1) return { id: null, name: nextName(saved.map((s) => s.series)) };
  return null;
}

/** The points a new series starts with: those on the picture that are not saved — they are no
 *  saved series' yet, so they are the new one's — and none when what is on the picture is a saved
 *  series as it was saved. */
export function carried(points: Pt[], unsaved: boolean): Pt[] {
  return unsaved ? points : [];
}

/** The name a new series is offered: "series N", N one more than the figure's saved series, and
 *  on past any name already taken — never the name of one that is saved. */
export function nextName(taken: string[]): string {
  let n = taken.length + 1;
  while (taken.includes(`series ${n}`)) n += 1;
  return `series ${n}`;
}

/** Whether a name is another saved series' of the figure than the one being saved (`active`, its
 *  id; null for a new one). A series is its id and its name a label, so the series being saved may
 *  keep its name or take a new one, but never one that is another's. */
export function nameTaken(saved: { id: number; series: string }[], active: number | null, name: string): boolean {
  return saved.some((s) => s.series === name && s.id !== active);
}

/** The point nearest (px, py) within `reach` pixels, or -1. */
export function nearest(points: Pt[], px: number, py: number, reach: number): number {
  let best = -1;
  let bestD = reach;
  points.forEach((p, i) => {
    const d = Math.hypot(p.px - px, p.py - py);
    if (d <= bestD) {
      best = i;
      bestD = d;
    }
  });
  return best;
}

export function addPoint(points: Pt[], px: number, py: number): Pt[] {
  return [...points, { px, py, how: 'added' }];
}

export function movePoint(points: Pt[], i: number, px: number, py: number): Pt[] {
  return points.map((p, j) => (j === i ? { px, py, how: p.how === 'added' ? 'added' : 'moved' } : p));
}

export function deletePoint(points: Pt[], i: number): Pt[] {
  return points.filter((_, j) => j !== i);
}

/** A colour as `#rrggbb`. */
export function hex(r: number, g: number, b: number): string {
  return `#${[r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')}`;
}

/** Whether a colour picked is the colour a series was found by, as finding its points takes it:
 *  within `tolerance` in RGB, the distance a pixel may be from the series' colour and count as it.
 *  A click on a curve takes one pixel's shade, so the same curve seldom gives the same `#rrggbb`
 *  twice. A series found by no colour is no colour's. */
export function sameCurve(found: string | null, picked: string, tolerance: number): boolean {
  if (!found) return false;
  const rgb = (c: string) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const [a, b] = [rgb(found), rgb(picked)];
  return Math.hypot(a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!) <= tolerance;
}

/** A number as a person reads it off a plot: four significant digits. */
export function num(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '';
  if (v === 0) return '0';
  const mag = Math.floor(Math.log10(Math.abs(v)));
  if (mag >= 6 || mag <= -4) return v.toExponential(digits - 1);
  return Number(v.toFixed(Math.max(0, digits - 1 - mag))).toString();
}

// ---- the fit, drawn ----------------------------------------------------------------------------------

export interface Interval {
  value: number;
  lo: number | null;
  hi: number | null;
  se?: number | null;
}

export interface FitResult {
  model: string;
  formula: string;
  status: 'fit' | 'unfit';
  reason: string | null;
  n: number;
  params: Record<string, Interval>;
  derived: Record<string, Interval>;
  r2?: number | null;
  rmse?: number | null;
  aic?: number | null;
  residuals?: { t: number; y: number; fit: number; residual: number }[];
  curve?: [number, number][];
}

export const QUANTITIES: [string, string, (xu: string, yu: string) => string][] = [
  ['plateau', 'Plateau', (_x, y) => y],
  ['max_rate', 'Maximum rate', (x, y) => (x || y ? `${y || 'y'}/${x || 't'}` : '')],
  ['t_half', 'Time to half maximum', (x) => x],
  ['lag', 'Lag time', (x) => x],
  ['baseline', 'Baseline', (_x, y) => y],
];

/** The quantities of a fit as table rows: name, value, interval, unit. */
export function fitRows(f: FitResult, xUnit = '', yUnit = ''): string[][] {
  return QUANTITIES.filter(([k]) => f.derived[k]).map(([k, label, unit]) => {
    const d = f.derived[k]!;
    const ci = d.lo !== null && d.hi !== null ? `${num(d.lo)} – ${num(d.hi)}` : '—';
    return [label, num(d.value), ci, unit(xUnit, yUnit)];
  });
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

function ticks(lo: number, hi: number, n = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / n;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => (hi - lo) / s <= n) ?? 10 * p;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9 * step; v += step) out.push(Number(v.toPrecision(10)));
  return out;
}

/** The points, the fitted curve, and the residuals in a strip under them, as one SVG. */
export function fitSvg(points: [number, number][], f: FitResult | null, opts: { width?: number; height?: number; xLabel?: string; yLabel?: string; colour?: string } = {}): string {
  const W = opts.width ?? 420;
  const H = opts.height ?? 300;
  const left = 52;
  const right = 12;
  const top = 10;
  const mainH = Math.round((H - top - 36) * 0.72);
  const gap = 10;
  const resH = H - top - 36 - mainH - gap;
  const colour = opts.colour ?? '#2f5d8a';
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]).concat((f?.curve ?? []).map((c) => c[1]));
  if (!xs.length) return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"></svg>`;
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const padY = (y1 - y0 || 1) * 0.06;
  const sx = (v: number) => left + ((v - x0) / (x1 - x0 || 1)) * (W - left - right);
  const sy = (v: number) => top + mainH - ((v - (y0 - padY)) / (y1 - y0 + 2 * padY || 1)) * mainH;
  const out: string[] = [`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-size="10" font-family="system-ui, sans-serif">`];
  out.push(`<rect x="${left}" y="${top}" width="${W - left - right}" height="${mainH}" fill="none" stroke="#d6d2c8"/>`);
  for (const t of ticks(y0 - padY, y1 + padY, 4)) out.push(`<text x="${left - 4}" y="${sy(t) + 3}" text-anchor="end" fill="#6b7280">${esc(num(t, 3))}</text><line x1="${left - 3}" x2="${left}" y1="${sy(t)}" y2="${sy(t)}" stroke="#9aa3ad"/>`);
  if (f?.curve?.length) out.push(`<polyline fill="none" stroke="#b3651c" stroke-width="1.6" points="${f.curve.map(([a, b]) => `${sx(a).toFixed(1)},${sy(b).toFixed(1)}`).join(' ')}"/>`);
  for (const [a, b] of points) out.push(`<circle cx="${sx(a).toFixed(1)}" cy="${sy(b).toFixed(1)}" r="2.6" fill="${colour}"/>`);
  if (f?.status === 'fit' && f.derived['lag'] && f.derived['t_half']) {
    const lag = f.derived['lag'].value;
    if (lag >= x0 && lag <= x1) out.push(`<line x1="${sx(lag)}" x2="${sx(lag)}" y1="${top}" y2="${top + mainH}" stroke="#7c6f9b" stroke-dasharray="3 3"/><text x="${sx(lag) + 3}" y="${top + 11}" fill="#7c6f9b">lag</text>`);
    const th = f.derived['t_half'].value;
    if (th >= x0 && th <= x1) out.push(`<line x1="${sx(th)}" x2="${sx(th)}" y1="${top}" y2="${top + mainH}" stroke="#2f7d5c" stroke-dasharray="3 3"/><text x="${sx(th) + 3}" y="${top + 22}" fill="#2f7d5c">t½</text>`);
  }
  // the residuals, under the points, on a scale of their own around zero
  const rt = top + mainH + gap;
  out.push(`<rect x="${left}" y="${rt}" width="${W - left - right}" height="${resH}" fill="none" stroke="#d6d2c8"/>`);
  const res = f?.residuals ?? [];
  if (res.length) {
    const rmax = Math.max(...res.map((r) => Math.abs(r.residual))) || 1;
    const ry = (v: number) => rt + resH / 2 - (v / rmax) * (resH / 2 - 3);
    out.push(`<line x1="${left}" x2="${W - right}" y1="${ry(0)}" y2="${ry(0)}" stroke="#9aa3ad"/>`);
    out.push(`<text x="${left - 4}" y="${ry(rmax) + 3}" text-anchor="end" fill="#6b7280">${esc(num(rmax, 2))}</text><text x="${left - 4}" y="${ry(-rmax) + 3}" text-anchor="end" fill="#6b7280">${esc(num(-rmax, 2))}</text>`);
    for (const r of res) out.push(`<line x1="${sx(r.t).toFixed(1)}" x2="${sx(r.t).toFixed(1)}" y1="${ry(0)}" y2="${ry(r.residual).toFixed(1)}" stroke="${colour}" stroke-opacity=".5"/><circle cx="${sx(r.t).toFixed(1)}" cy="${ry(r.residual).toFixed(1)}" r="2" fill="${colour}"/>`);
  }
  out.push(`<text x="${left - 40}" y="${rt + resH / 2 + 3}" fill="#6b7280" transform="rotate(-90 ${left - 40} ${rt + resH / 2})" text-anchor="middle">residual</text>`);
  for (const t of ticks(x0, x1, 6)) out.push(`<text x="${sx(t)}" y="${rt + resH + 13}" text-anchor="middle" fill="#6b7280">${esc(num(t, 3))}</text><line x1="${sx(t)}" x2="${sx(t)}" y1="${rt + resH}" y2="${rt + resH + 3}" stroke="#9aa3ad"/>`);
  if (opts.xLabel) out.push(`<text x="${(left + W - right) / 2}" y="${H - 4}" text-anchor="middle" fill="#1f2328">${esc(opts.xLabel)}</text>`);
  if (opts.yLabel) out.push(`<text x="12" y="${top + mainH / 2}" transform="rotate(-90 12 ${top + mainH / 2})" text-anchor="middle" fill="#1f2328">${esc(opts.yLabel)}</text>`);
  out.push('</svg>');
  return out.join('');
}

/** A file name for a figure's export: the paper's key and the figure's number, safe on every system. */
export function exportName(paper: string | null, label: string | null, series: string): string {
  const parts = [paper ?? 'image', label ? `fig${label}` : '', series].filter(Boolean).join('_');
  return `${parts.replace(/[^A-Za-z0-9._-]+/g, '_').slice(0, 120)}.csv`;
}
