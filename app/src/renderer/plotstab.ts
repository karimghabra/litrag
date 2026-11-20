/**
 * The Plots tab: the figures that are data plots, found by the words of their captions
 * (`plots`, parser/litrag_parser/digitise.py `find`), and one of them digitised — WebPlotDigitizer's
 * workflow on the figure's own pixels. The axes: two points of known value each, offered from the
 * numbers beside them when the worker can read them, always for a person to confirm or move. The
 * series: picked by its colour — a click on it, or one of the figure's own colours — and its points
 * found where that colour is (`digitise_extract`), then added, moved and deleted by hand. Saved,
 * a series is rows; exported, a CSV with a JSON sidecar that traces each value to its pixel; fitted,
 * a logistic or Gompertz curve with its lag, rate, midpoint and plateau. The arithmetic is
 * digitise.ts's; this module draws and listens.
 */

import { Calibration, addPoint, charted, deletePoint, exportName, fitRows, fitSvg, hex, movePoint, nameTaken, nearest, nextName, num, onPicture, sameCurve, tryCalibration, valued, type AxisCal, type CalibrationDict, type FitResult, type Pt } from './digitise.ts';
import { $, activity, ctx, el, hooks, log, onProjectChange, onViewShown, projectName, rejectionText, request, showView } from './shared.ts';

interface FoundFigure {
  figure: string;
  paper: string;
  title: string;
  year: string | null;
  label: string | null;
  caption: string;
  plot: 'plot' | 'no plot' | 'unknown';
  trend: boolean;
  plots: { kind: string | null; status: string; x_scale: string | null; y_label: string | null }[];
  digitised: number;
  image: boolean;
  format: string;
}

interface AxisOffer {
  axis: AxisCal | null;
  ticks: number;
  residual: number | null;
  log: boolean;
  label?: string | null;
  unit?: string | null;
  reason?: string;
}

interface Opened {
  figure?: string;
  source: { paper: string | null; node: string | null; label: string | null; title: string | null; how: string | null; reason: string | null; file: string | null; page: number | null; bbox: number[] | null };
  image: { path: string | null; width: number; height: number; scale: number; sha256: string } | null;
  auto: { found: boolean; confident: boolean; calibration: CalibrationDict | null; plot_region?: number[]; frame?: number[]; reason: string | null; x: AxisOffer; y: AxisOffer; words?: string } | null;
  palette: { colour: string; share: number }[];
  saved: SavedSeries[];
  /** The paper's series to review — it no longer draws the picture they were read from: listed apart
   *  with that picture, never loaded onto this one, put on a figure only by a person. */
  orphans: SavedSeries[];
}

interface SavedSeries {
  id: number;
  figure: string;
  figure_label: string | null;
  series: string;
  colour: string | null;
  calibration: CalibrationDict;
  method: Record<string, unknown>;
  image: { sha256?: string };
  /** For a series to review: the file of the picture it was read from. */
  picture?: string | null;
  axes: { x: { label: string | null; unit: string | null }; y: { label: string | null; unit: string | null } };
  points: Pt[];
  fits: Record<string, FitResult>;
}

type Tool = 'colour' | 'add' | 'move' | 'delete' | 'region';
type Place = 'x1' | 'x2' | 'y1' | 'y2';

const st = {
  opened: null as Opened | null,
  asked: 0,
  figure: null as string | null,
  imagePath: null as string | null,
  pixels: null as ImageData | null,
  zoom: 1,
  cal: { x: { log: false } as Partial<AxisCal>, y: { log: false } as Partial<AxisCal> },
  fromAuto: false,
  confirmed: false,
  placing: null as Place | null,
  tool: 'colour' as Tool,
  region: null as number[] | null,
  colour: null as string | null,
  points: [] as Pt[],
  method: null as Record<string, unknown> | null,
  activeSaved: null as number | null,
  dirty: false,
  fit: null as FitResult | null,
  drag: null as { kind: 'move'; i: number } | { kind: 'region'; x: number; y: number } | null,
  list: [] as FoundFigure[],
};

const W = () => st.opened?.image?.width ?? 0;
const H = () => st.opened?.image?.height ?? 0;

function calibration(): Calibration | null {
  return tryCalibration(st.cal as Partial<{ x: Partial<AxisCal>; y: Partial<AxisCal> }>).cal;
}

// ---- finding plots -----------------------------------------------------------------------------------

async function find(): Promise<void> {
  if (!ctx.lib) return;
  const query = $<HTMLInputElement>('plots-q').value.trim();
  const trend = $<HTMLInputElement>('plots-trend').checked;
  $('plots-total').textContent = 'looking…';
  try {
    const r = await request<{ figures: FoundFigure[]; total: number }>('plots', { lib: ctx.lib, query, trend, limit: 200 });
    st.list = r.figures;
    $('plots-total').textContent = `${r.total} figure${r.total === 1 ? '' : 's'}${r.total > r.figures.length ? `, the first ${r.figures.length}` : ''}`;
    renderList();
  } catch (e) {
    $('plots-total').textContent = '';
    log('error', `plots: ${rejectionText(e)}`);
  }
}

function renderList(): void {
  const box = $('plots-list');
  box.innerHTML = '';
  if (!st.list.length) {
    box.append(el('div', 'empty', 'No figure’s caption has those words. Try fewer, or untick “trends only”.'));
    return;
  }
  for (const f of st.list) {
    const card = el('div', `plot-card${f.figure === st.figure ? ' on' : ''}`);
    const what = el('div', 'what');
    what.append(el('span', `pill ${f.plot === 'no plot' ? 'noplot' : f.plot}`, f.plot));
    if (f.trend) what.append(el('span', 'pill trend', 'trend'));
    if (f.digitised) what.append(el('span', 'pill', `${f.digitised} digitised`));
    if (!f.image) what.append(el('span', 'pill closed', 'no image'));
    what.append(el('span', 'paper', `${f.label ? `Figure ${f.label}` : 'Figure'} · ${(f.title || f.paper).slice(0, 70)}${f.year ? ` (${f.year})` : ''}`));
    card.append(what, el('div', 'cap', f.caption));
    card.title = f.image ? 'Open this figure to digitise it' : 'No PDF draws this figure here: an XML paper needs the PDF kept beside it (the Papers tab asks PMC for one)';
    card.addEventListener('click', () => void openFigure(f.figure));
    box.append(card);
  }
}

// ---- a figure, opened -------------------------------------------------------------------------------

function reset(): void {
  st.cal = { x: { log: false }, y: { log: false } };
  st.fromAuto = false;
  st.confirmed = false;
  st.placing = null;
  st.region = null;
  setColour(null);
  st.points = [];
  st.method = null;
  st.activeSaved = null;
  st.dirty = false;
  st.fit = null;
  for (const id of ['dg-xlabel', 'dg-xunit', 'dg-ylabel', 'dg-yunit', 'dg-name']) $<HTMLInputElement>(id).value = '';
}

const asked: Record<string, number> = {};

/** A request's place among those of its kind: `latest()` holds until another of the kind is asked
 *  or another figure is shown. The worker answers some on threads of their own, so an earlier
 *  request's answer can come last, and only the latest one's is taken. */
function ticket(kind: 'axes' | 'extract' | 'fit'): () => boolean {
  const n = (asked[kind] = (asked[kind] ?? 0) + 1);
  const opened = st.opened;
  return () => asked[kind] === n && st.opened === opened;
}

/** Opens what `req` names and shows it — unless another open was asked for since, or another
 *  project chosen: the worker answers opens on their own threads, so an earlier one's answer
 *  can come last. */
async function open(req: Record<string, unknown>, doing: string): Promise<void> {
  const mine = ++st.asked;
  const lib = ctx.lib;
  activity.show(doing);
  try {
    const r = await request<Opened>('digitise_open', req);
    if (mine === st.asked && lib === ctx.lib) await show(r);
  } catch (e) {
    if (mine === st.asked) log('error', `digitise: ${rejectionText(e)}`);
  } finally {
    if (mine === st.asked) activity.hide();
  }
}

async function openFigure(figure: string): Promise<void> {
  if (!ctx.lib) return;
  await open({ lib: ctx.lib, figure }, 'Cutting the figure from its page and reading its axes');
}

async function openImage(): Promise<void> {
  const path = await window.litrag.chooseImage();
  if (!path) return;
  await open({ lib: ctx.lib ?? undefined, path }, 'Reading the image’s axes');
}

async function show(r: Opened): Promise<void> {
  reset();
  st.opened = r;
  st.figure = r.figure ?? null;
  renderList();
  const src = r.source;
  $('dg-title').textContent = src.node ? `${src.label ? `Figure ${src.label}` : 'A figure'} · ${(src.title ?? '').slice(0, 60)}` : `Image · ${src.title ?? ''}`;
  const cap = st.list.find((f) => f.figure === src.node)?.caption ?? '';
  $('dg-caption').textContent = cap;
  if (!r.image?.path) {
    $('dg-canvas-wrap').hidden = true;
    $('dg-empty').hidden = false;
    $('dg-empty').textContent = `This figure cannot be drawn here: ${src.reason ?? 'no image'}`;
    st.imagePath = null;
    st.pixels = null;
    renderAll();
    return;
  }
  st.imagePath = r.image.path;
  const bytes = await window.litrag.readFile(r.image.path);
  const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
  if (st.opened !== r) {
    bitmap.close();
    return;
  }
  const canvas = $<HTMLCanvasElement>('dg-canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const g = canvas.getContext('2d', { willReadFrequently: true })!;
  g.drawImage(bitmap, 0, 0);
  st.pixels = g.getImageData(0, 0, bitmap.width, bitmap.height);
  bitmap.close();
  $('dg-empty').hidden = true;
  $('dg-canvas-wrap').hidden = false;
  zoomFit();
  const a = r.auto;
  if (a?.found && a.calibration) {
    applyAuto();
  }
  for (const [axis, lab, unit] of [['x', 'dg-xlabel', 'dg-xunit'], ['y', 'dg-ylabel', 'dg-yunit']] as const) {
    const o = a?.[axis];
    if (o?.label) $<HTMLInputElement>(lab).value = o.label.replace(/\s*\([^()]*\)\s*$/, '');
    if (o?.unit) $<HTMLInputElement>(unit).value = o.unit;
  }
  if (a?.plot_region) st.region = a.plot_region;
  setTool('colour');
  if (r.saved.length) loadSaved(r.saved[0]!.id);
  renderAll();
}

function applyAuto(): void {
  const a = st.opened?.auto;
  if (!a?.calibration) return;
  st.cal = { x: { ...a.calibration.x }, y: { ...a.calibration.y } };
  st.fromAuto = true;
  st.confirmed = false;
  renderAxes();
  drawOverlay();
}

// ---- the stage ----------------------------------------------------------------------------------------

function setZoom(z: number): void {
  st.zoom = Math.max(0.1, Math.min(8, z));
  const canvas = $<HTMLCanvasElement>('dg-canvas');
  canvas.style.width = `${W() * st.zoom}px`;
  canvas.style.height = `${H() * st.zoom}px`;
  drawOverlay();
}

function zoomFit(): void {
  const stage = $('dg-stage');
  if (!W()) return;
  setZoom(Math.min((stage.clientWidth - 20) / W(), (stage.clientHeight - 20) / H(), 2) || 1);
}

function setTool(t: Tool): void {
  st.tool = t;
  st.placing = null;
  for (const b of document.querySelectorAll<HTMLButtonElement>('#dg-tools [data-tool]')) b.classList.toggle('on', b.dataset['tool'] === t);
  for (const b of document.querySelectorAll<HTMLButtonElement>('.dg-place')) b.classList.remove('on');
  $('dg-stage').className = `dg-stage tool-${t}`;
  hint();
}

function setPlacing(p: Place | null): void {
  st.placing = p;
  for (const b of document.querySelectorAll<HTMLButtonElement>('.dg-place')) b.classList.toggle('on', b.dataset['point'] === st.placing);
  $('dg-stage').className = `dg-stage tool-${st.placing ? 'place' : st.tool}`;
  hint();
}

function hint(): void {
  const h = st.placing
    ? `Click where ${st.placing.toUpperCase()} is on the figure${st.placing[0] === 'x' ? ' — a tick on the x axis' : ' — a tick on the y axis'}, then type its value`
    : ({ colour: 'Click the series’ line or a marker to take its colour', add: 'Click to add a point', move: 'Drag a point', delete: 'Click a point to delete it', region: 'Drag a box over one plot: its axes are read again, and the series is looked for inside it' } as Record<Tool, string>)[st.tool];
  $('dg-hint').textContent = h;
}

function at(ev: MouseEvent): [number, number] {
  const r = $<HTMLCanvasElement>('dg-canvas').getBoundingClientRect();
  return [(ev.clientX - r.left) / st.zoom, (ev.clientY - r.top) / st.zoom];
}

function pickColour(px: number, py: number): string | null {
  const d = st.pixels;
  if (!d) return null;
  // the darkest, most saturated pixel near the click: a thin line is easy to miss by one
  let best: [number, number, number] | null = null;
  let score = -1;
  for (let dy = -2; dy <= 2; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      const x = Math.round(px) + dx;
      const y = Math.round(py) + dy;
      if (x < 0 || y < 0 || x >= d.width || y >= d.height) continue;
      const i = (y * d.width + x) * 4;
      const [r, g, b] = [d.data[i]!, d.data[i + 1]!, d.data[i + 2]!];
      const ink = 765 - (r + g + b) + 2 * (Math.max(r, g, b) - Math.min(r, g, b));
      if (ink > score) {
        score = ink;
        best = [r, g, b];
      }
    }
  }
  return best && score > 40 ? hex(...best) : null;
}

function onDown(ev: MouseEvent): void {
  if (!st.opened?.image || ev.button !== 0) return;
  const [px, py] = at(ev);
  const reach = 8 / st.zoom;
  if (st.placing) {
    const p = st.placing;
    const axis = p[0] as 'x' | 'y';
    (st.cal[axis] as Partial<AxisCal>)[p[1] === '1' ? 'p1' : 'p2'] = [px, py];
    st.fromAuto = false;
    st.confirmed = false;
    // the next of the four that is not set yet, while a calibration is being made from nothing
    const order: Place[] = ['x1', 'x2', 'y1', 'y2'];
    const unset = order.filter((q) => !st.cal[q[0] as 'x' | 'y'][q[1] === '1' ? 'p1' : 'p2']);
    setPlacing(unset[0] ?? null);
    axesChanged();
    document.querySelector<HTMLInputElement>(`.dg-val[data-val="${p}"]`)?.focus();
    return;
  }
  if (st.tool === 'colour') {
    const c = pickColour(px, py);
    if (c) pickedColour(c);
    return;
  }
  if (st.tool === 'add') {
    st.points = addPoint(st.points, px, py);
    changed();
    return;
  }
  if (st.tool === 'delete') {
    const i = nearest(st.points, px, py, reach);
    if (i >= 0) {
      st.points = deletePoint(st.points, i);
      changed();
    }
    return;
  }
  if (st.tool === 'move') {
    const i = nearest(st.points, px, py, reach);
    if (i >= 0) st.drag = { kind: 'move', i };
    return;
  }
  if (st.tool === 'region') st.drag = { kind: 'region', x: px, y: py };
}

function onMove(ev: MouseEvent): void {
  if (!st.drag) return;
  const [px, py] = at(ev);
  if (st.drag.kind === 'move') {
    st.points = movePoint(st.points, st.drag.i, px, py);
    drawOverlay();
  } else {
    st.region = [Math.min(st.drag.x, px), Math.min(st.drag.y, py), Math.max(st.drag.x, px), Math.max(st.drag.y, py)];
    drawOverlay();
  }
}

function onUp(): void {
  if (!st.drag) return;
  const was = st.drag;
  st.drag = null;
  if (was.kind === 'move') changed();
  else if (st.region && (st.region[2]! - st.region[0]! < 5 || st.region[3]! - st.region[1]! < 5)) {
    st.region = st.opened?.auto?.plot_region ?? null;
    drawOverlay();
  } else if (st.region) void readPanel(st.region);
}

/** The axes read again, of the plot the region was drawn over: a figure of several panels offers
 *  the largest first, and a person points at the one they want. A calibration they set is kept. */
async function readPanel(region: number[]): Promise<void> {
  const opened = st.opened;
  if (!st.imagePath || !opened) return;
  const near = [(region[0]! + region[2]!) / 2, (region[1]! + region[3]!) / 2];
  const latest = ticket('axes');
  try {
    const r = await request<{ auto: NonNullable<Opened['auto']> }>('digitise_axes', { image: st.imagePath, near });
    if (!latest()) return;
    opened.auto = r.auto;
    const mine = !st.fromAuto && tryCalibration(st.cal).cal !== null;
    if (r.auto.found && !mine) {
      applyAuto();
      axesChanged();
      for (const [axis, lab, unit] of [['x', 'dg-xlabel', 'dg-xunit'], ['y', 'dg-ylabel', 'dg-yunit']] as const) {
        const o = r.auto[axis];
        if (o?.label) $<HTMLInputElement>(lab).value = o.label.replace(/\s*\([^()]*\)\s*$/, '');
        if (o?.unit) $<HTMLInputElement>(unit).value = o.unit;
      }
    }
    renderAxes();
    renderPoints();
    renderFit();
  } catch (e) {
    log('error', `axes: ${rejectionText(e)}`);
  }
}

function changed(): void {
  st.dirty = true;
  st.fit = null;
  renderPoints();
  drawOverlay();
  renderFit();
}

/** The axes were edited — a point placed, a value typed, a scale ticked, the offer taken, a label
 *  or a unit changed: every value reads differently now, so what is saved is not what is shown,
 *  and a fit of the old values is no fit of these. */
function axesChanged(): void {
  renderAxes();
  changed();
}

/** The working series put aside for a new one: nothing of it on the picture, no saved series
 *  being edited, no colour it was found by, the next default name. The saved ones stay as they are. */
function newSeries(): void {
  st.activeSaved = null;
  setColour(null);
  st.points = [];
  st.method = null;
  st.fit = null;
  st.dirty = false;
  $<HTMLInputElement>('dg-name').value = nextName((st.opened?.saved ?? []).map((s) => s.series));
}

/** A colour that is not the one the saved series being edited was found by — not within the
 *  tolerance its points are found with — is another curve's: what is found by it is a new series,
 *  not that one's points. */
function anotherCurve(colour: string): boolean {
  const s = st.opened?.saved.find((x) => x.id === st.activeSaved);
  return !!s && !sameCurve(s.colour, colour, Number($<HTMLInputElement>('dg-tol').value));
}

/** A colour a person picked. Another curve's starts a new series, unless edits to the one on the
 *  picture are not saved yet: those stay until points are found by the new colour. */
function pickedColour(c: string): void {
  if (!st.dirty && anotherCurve(c)) {
    newSeries();
    renderAll();
  }
  setColour(c);
}

/** The colour the working series is found by, and the swatch that shows it: none, for a series
 *  found by none. */
function setColour(c: string | null): void {
  st.colour = c;
  $('dg-swatch').style.background = c ?? '';
  $('dg-swatch').title = c ? `The series’ colour: ${c}` : 'The colour the series is found by';
}

const SVGNS = 'http://www.w3.org/2000/svg';
function svg(tag: string, attrs: Record<string, string | number>, text?: string): SVGElement {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  if (text !== undefined) e.textContent = text;
  return e;
}

function drawOverlay(): void {
  const o = $('dg-overlay') as unknown as SVGSVGElement;
  o.innerHTML = '';
  if (!W()) return;
  o.setAttribute('viewBox', `0 0 ${W()} ${H()}`);
  o.setAttribute('width', String(W() * st.zoom));
  o.setAttribute('height', String(H() * st.zoom));
  const k = 1 / st.zoom; // a stroke's width, in image pixels, that draws one screen pixel
  if (st.region) {
    const [x0, y0, x1, y1] = st.region as [number, number, number, number];
    o.append(svg('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: 'none', stroke: '#7c6f9b', 'stroke-width': k, 'stroke-dasharray': `${4 * k} ${3 * k}` }));
  }
  const axisColour = { x: '#2f5d8a', y: '#2f7d5c' };
  for (const a of ['x', 'y'] as const) {
    const c = st.cal[a];
    if (c.p1 && c.p2) o.append(svg('line', { x1: c.p1[0], y1: c.p1[1], x2: c.p2[0], y2: c.p2[1], stroke: axisColour[a], 'stroke-width': k, 'stroke-dasharray': `${2 * k} ${2 * k}`, opacity: 0.7 }));
    for (const [which, p] of [['1', c.p1], ['2', c.p2]] as const) {
      if (!p) continue;
      const s = 7 * k;
      o.append(svg('path', { d: `M${p[0] - s},${p[1]}H${p[0] + s}M${p[0]},${p[1] - s}V${p[1] + s}`, stroke: axisColour[a], 'stroke-width': 1.5 * k }));
      o.append(svg('text', { x: p[0] + 4 * k, y: p[1] - 4 * k, fill: axisColour[a], 'font-size': 11 * k, 'font-weight': 700 }, `${a.toUpperCase()}${which}`));
    }
  }
  // saved series other than the one being edited, faint
  for (const s of st.opened?.saved ?? []) {
    if (s.id === st.activeSaved) continue;
    for (const p of onPicture(s, st.opened?.image?.sha256).points) o.append(svg('circle', { cx: p.px, cy: p.py, r: 2.5 * k, fill: s.colour ?? '#999', opacity: 0.45 }));
  }
  for (const p of st.points) {
    o.append(svg('circle', { cx: p.px, cy: p.py, r: 4 * k, fill: 'none', stroke: '#fff', 'stroke-width': 3 * k }));
    o.append(svg('circle', { cx: p.px, cy: p.py, r: 4 * k, fill: 'none', stroke: p.how === 'auto' ? '#d1004f' : '#0a7d3e', 'stroke-width': 1.6 * k }));
  }
}

// ---- the panel ----------------------------------------------------------------------------------------

function renderAxes(): void {
  for (const a of ['x', 'y'] as const) {
    for (const i of ['1', '2'] as const) {
      const v = st.cal[a][i === '1' ? 'v1' : 'v2'];
      const input = document.querySelector<HTMLInputElement>(`.dg-val[data-val="${a}${i}"]`)!;
      if (document.activeElement !== input) input.value = v === undefined || !Number.isFinite(v) ? '' : String(v);
      const btn = document.querySelector<HTMLButtonElement>(`.dg-place[data-point="${a}${i}"]`)!;
      btn.title = st.cal[a][i === '1' ? 'p1' : 'p2'] ? `${a.toUpperCase()}${i} is set: click to place it again` : `Click on the figure where ${a.toUpperCase()}${i} is`;
    }
    document.querySelector<HTMLInputElement>(`.dg-log[data-log="${a}"]`)!.checked = !!st.cal[a].log;
  }
  const a = st.opened?.auto;
  const autoLine = !st.opened ? '' : a?.found ? `read from the figure (${a.words === 'ocr' ? 'OCR' : 'its text'}): ${a.x.ticks} x ticks, ${a.y.ticks} y ticks${a.confident ? '' : ' — not sure of it'}` : a?.reason ? `not read: ${a.reason}` : '';
  $('dg-auto').textContent = autoLine;
  $<HTMLButtonElement>('dg-use-auto').hidden = !a?.found;
  const { cal, why } = tryCalibration(st.cal);
  const state = $('dg-cal-state');
  if (!cal) state.textContent = why ?? '';
  else if (!st.confirmed) {
    state.textContent = '';
    const b = el('button', 'primary small', st.fromAuto ? 'Confirm these axes' : 'Confirm axes') as HTMLButtonElement;
    b.title = 'The four points sit on the right ticks and the values are theirs';
    b.addEventListener('click', () => {
      st.confirmed = true;
      renderAxes();
      renderPoints();
    });
    state.append(b);
  } else state.textContent = 'axes confirmed';
}

function renderSaved(): void {
  const box = $('dg-saved');
  box.innerHTML = '';
  for (const s of st.opened?.saved ?? []) {
    const row = el('div', `saved${s.id === st.activeSaved ? ' on' : ''}`);
    const dot = el('span', 'dot');
    dot.style.background = s.colour ?? '#999';
    row.append(dot, el('span', undefined, s.series), el('span', 'muted', `${s.points.length} points${Object.keys(s.fits).length ? ` · fit: ${Object.keys(s.fits).join(', ')}` : ''}${onPicture(s, st.opened?.image?.sha256).same ? '' : ' · read from another picture'}`));
    const del = el('button', 'ghost small', '×') as HTMLButtonElement;
    del.title = 'Delete this saved series';
    del.addEventListener('click', (ev) => {
      ev.stopPropagation();
      void deleteSaved(s.id);
    });
    row.append(el('span', 'spacer'), del);
    row.title = 'Edit this series';
    row.addEventListener('click', () => loadSaved(s.id));
    box.append(row);
  }
  const orphans = st.opened?.orphans ?? [];
  if (orphans.length) box.append(el('div', 'muted', 'Needs review — the paper no longer draws the picture these were read from. Kept as saved; put one on this figure if it is its own:'));
  for (const s of orphans) {
    const row = el('div', 'orphan');
    if (s.picture) {
      const img = el('img') as HTMLImageElement;
      img.alt = 'the picture it was read from';
      void thumb(s.picture).then((url) => (img.src = url));
      row.append(img);
    }
    const dot = el('span', 'dot');
    dot.style.background = s.colour ?? '#999';
    row.append(dot, el('span', undefined, s.series), el('span', 'muted', `${s.points.length} points · was on ${s.figure_label ? `Figure ${s.figure_label}` : 'a figure with no number'}`), el('span', 'spacer'));
    if (st.figure) {
      const put = el('button', 'ghost small', 'Put on this figure') as HTMLButtonElement;
      put.title = 'This series is of the figure shown: keep it with it from now on. Its saved values are not changed';
      put.addEventListener('click', () => void attachSaved(s.id));
      row.append(put);
    }
    const del = el('button', 'ghost small', '×') as HTMLButtonElement;
    del.title = 'Delete this series';
    del.addEventListener('click', () => void deleteSaved(s.id));
    row.append(del);
    box.append(row);
  }
}

const thumbs = new Map<string, Promise<string>>();

/** A picture file as a URL an <img> can show, read once. */
function thumb(path: string): Promise<string> {
  let url = thumbs.get(path);
  if (!url) {
    url = window.litrag.readFile(path).then((bytes) => URL.createObjectURL(new Blob([bytes], { type: 'image/png' })));
    thumbs.set(path, url);
  }
  return url;
}

function loadSaved(id: number): void {
  const s = st.opened?.saved.find((x) => x.id === id);
  if (!s) return;
  const on = onPicture(s, st.opened?.image?.sha256);
  st.activeSaved = id;
  if (on.calibration) {
    st.cal = on.calibration;
    st.fromAuto = false;
  }
  st.confirmed = on.same;
  st.points = on.points;
  st.method = s.method;
  st.dirty = false;
  setColour(s.colour);
  $<HTMLInputElement>('dg-name').value = s.series;
  $<HTMLInputElement>('dg-xlabel').value = s.axes.x.label ?? '';
  $<HTMLInputElement>('dg-xunit').value = s.axes.x.unit ?? '';
  $<HTMLInputElement>('dg-ylabel').value = s.axes.y.label ?? '';
  $<HTMLInputElement>('dg-yunit').value = s.axes.y.unit ?? '';
  const model = $<HTMLSelectElement>('dg-model').value;
  st.fit = s.fits[model] ?? Object.values(s.fits)[0] ?? null;
  renderAll();
}

function renderPoints(): void {
  const cal = calibration();
  const pts = valued(st.points, cal);
  $('dg-count').textContent = st.points.length ? `${st.points.length} point${st.points.length === 1 ? '' : 's'}${st.dirty ? ' · not saved' : ''}` : '';
  const box = $('dg-points');
  box.innerHTML = '';
  if (!pts.length) {
    const s = st.opened?.saved.find((x) => x.id === st.activeSaved);
    if (s && !onPicture(s, st.opened?.image?.sha256).same) {
      box.append(el('div', 'warn', `The picture of this figure has changed since “${s.series}” was digitised. Its ${s.points.length} saved point${s.points.length === 1 ? '' : 's'} are kept as they were, still export, and are not drawn on this picture: confirm the axes on it before finding new points.`));
      return;
    }
    box.append(el('div', 'muted', 'No points yet: pick the series’ colour and Find points, or add them by hand.'));
    return;
  }
  const xh = axisHead('x');
  const yh = axisHead('y');
  const table = el('table');
  const head = el('tr');
  for (const h of ['#', xh, yh, 'pixel']) head.append(el('th', undefined, h));
  table.append(head);
  pts.forEach((p, i) => {
    const tr = el('tr', `how-${p.how}`);
    tr.append(el('td', undefined, String(i + 1)), el('td', undefined, num(p.x)), el('td', undefined, num(p.y)), el('td', 'muted', `${p.px.toFixed(0)}, ${p.py.toFixed(0)}`));
    tr.title = p.how === 'auto' ? 'found by its colour' : p.how === 'added' ? 'added by hand' : 'moved by hand';
    table.append(tr);
  });
  box.append(table);
}

function axisHead(a: 'x' | 'y'): string {
  const label = $<HTMLInputElement>(a === 'x' ? 'dg-xlabel' : 'dg-ylabel').value.trim() || a;
  const unit = $<HTMLInputElement>(a === 'x' ? 'dg-xunit' : 'dg-yunit').value.trim();
  return unit ? `${label} (${unit})` : label;
}

function renderFit(): void {
  const box = $('dg-fit-out');
  box.innerHTML = '';
  const cal = calibration();
  const pts = charted(valued(st.points, cal), st.opened?.saved.find((x) => x.id === st.activeSaved), st.opened?.image?.sha256);
  const f = st.fit;
  if (!f) {
    if (pts.length) box.insertAdjacentHTML('beforeend', fitSvg(pts, null, { xLabel: axisHead('x'), yLabel: axisHead('y'), colour: st.colour ?? undefined }));
    return;
  }
  if (f.status !== 'fit') {
    box.append(el('div', 'warn', `Not fitted: ${f.reason ?? 'no reason given'}`));
    return;
  }
  const xu = $<HTMLInputElement>('dg-xunit').value.trim();
  const yu = $<HTMLInputElement>('dg-yunit').value.trim();
  const table = el('table');
  const head = el('tr');
  for (const h of ['', 'value', '95% interval', 'unit']) head.append(el('th', undefined, h));
  table.append(head);
  for (const row of fitRows(f, xu, yu)) {
    const tr = el('tr');
    row.forEach((c) => tr.append(el('td', undefined, c)));
    table.append(tr);
  }
  box.append(table);
  box.append(el('div', 'muted', `${f.model} · ${f.formula} · n = ${f.n} · R² = ${num(f.r2 ?? null, 4)} · RMSE = ${num(f.rmse ?? null, 3)}`));
  if (f.reason) box.append(el('div', 'warn', f.reason));
  box.insertAdjacentHTML('beforeend', fitSvg(pts, f, { xLabel: axisHead('x'), yLabel: axisHead('y'), colour: st.colour ?? undefined }));
}

function renderPalette(): void {
  const box = $('dg-palette');
  box.innerHTML = '';
  for (const p of st.opened?.palette ?? []) {
    const b = el('button', 'ghost') as HTMLButtonElement;
    b.style.background = p.colour;
    b.title = `${p.colour}: ${(100 * p.share).toFixed(1)}% of the plot’s ink`;
    b.addEventListener('click', () => pickedColour(p.colour));
    box.append(b);
  }
}

function renderAll(): void {
  const has = !!st.opened?.image;
  for (const id of ['dg-extract', 'dg-save', 'dg-new', 'dg-clear', 'dg-export', 'dg-fit', 'dg-use-auto']) $<HTMLButtonElement>(id).disabled = !has;
  renderAxes();
  renderPalette();
  renderSaved();
  renderPoints();
  renderFit();
  drawOverlay();
  hint();
}

// ---- the work -----------------------------------------------------------------------------------------

async function extract(): Promise<void> {
  if (!st.imagePath) return;
  if (!st.colour) {
    log('error', 'Pick the series’ colour first: click its line or a marker, or one of the colours offered');
    return;
  }
  const cal = calibration();
  const mode = $<HTMLSelectElement>('dg-mode').value;
  const tolerance = Number($<HTMLInputElement>('dg-tol').value);
  const colour = st.colour;
  const latest = ticket('extract');
  try {
    const r = await request<{ mode: string; points: Pt[]; legend: number; pixels: number; region: number[] }>('digitise_extract', {
      image: st.imagePath, colour, tolerance, mode, region: st.region ?? undefined, calibration: cal ? cal.c : undefined,
    });
    if (!latest()) return;
    if (anotherCurve(colour)) {
      newSeries();
      setColour(colour);
      renderSaved();
    }
    st.points = r.points.map((p) => ({ px: p.px, py: p.py, how: 'auto' as const }));
    st.method = { mode: r.mode, asked: mode, tolerance, region: r.region, colour, auto_axes: st.fromAuto };
    changed();
    log('info', `${r.points.length} points found as ${r.mode === 'line' ? 'a line' : 'markers'} (${r.pixels} pixels of ${st.colour}${r.legend ? `; ${r.legend} legend marker${r.legend === 1 ? '' : 's'} left out` : ''})`);
  } catch (e) {
    log('error', `digitise: ${rejectionText(e)}`);
  }
}

function axesOf(): Record<string, unknown> {
  return {
    x: { label: $<HTMLInputElement>('dg-xlabel').value.trim() || null, unit: $<HTMLInputElement>('dg-xunit').value.trim() || null },
    y: { label: $<HTMLInputElement>('dg-ylabel').value.trim() || null, unit: $<HTMLInputElement>('dg-yunit').value.trim() || null },
  };
}

function ready(what: string): Calibration | null {
  const { cal, why } = tryCalibration(st.cal);
  if (!cal) {
    log('error', `${what}: the axes do not hold yet — ${why}`);
    return null;
  }
  if (!st.confirmed) {
    log('error', `${what}: confirm the axes first — check the four points sit on their ticks`);
    return null;
  }
  return cal;
}

async function save(): Promise<SavedSeries | null> {
  const cal = ready('Save');
  if (!cal || !ctx.lib || !st.figure) {
    if (cal && !st.figure) log('error', 'Save: an image opened from a file is not a figure of the library; Export CSV writes its points');
    return null;
  }
  const opened = st.opened;
  if (!opened) return null;
  if (!st.points.length) {
    log('error', 'Save: there are no points to save');
    return null;
  }
  const name = $<HTMLInputElement>('dg-name').value.trim() || nextName(opened.saved.map((s) => s.series));
  $<HTMLInputElement>('dg-name').value = name;
  const old = opened.saved.find((s) => s.id === st.activeSaved);
  if (nameTaken(opened.saved, old?.id ?? null, name)) {
    log('error', `Save: this figure has another series named “${name}”. Give this one a name of its own`);
    return null;
  }
  if (old && !window.confirm(`Save over the saved series “${old.series}” (${old.points.length} point${old.points.length === 1 ? '' : 's'}) with the ${st.points.length} on the picture${old.series === name ? '' : `, as “${name}”`}? To keep it as it is and save these beside it, cancel and click New series.`)) return null;
  try {
    const r = await request<{ series: SavedSeries }>('digitise_save', {
      lib: ctx.lib, figure: st.figure, series: name, calibration: cal.c, points: st.points, colour: st.colour, method: st.method ?? { mode: 'manual' }, image: opened.image, axes: axesOf(), source: opened.source, series_id: old?.id,
    });
    if (st.opened !== opened) return null;
    opened.saved = [...opened.saved.filter((s) => s.id !== r.series.id), r.series];
    st.activeSaved = r.series.id;
    st.dirty = false;
    renderSaved();
    renderPoints();
    log('info', `Saved “${name}”: ${r.series.points.length} points`);
    const f = st.list.find((x) => x.figure === st.figure);
    if (f) {
      f.digitised = opened.saved.length;
      renderList();
    }
    if (st.fit) await fitSaved(r.series.id, st.fit.model);
    return r.series;
  } catch (e) {
    log('error', `save: ${rejectionText(e)}`);
    return null;
  }
}

async function deleteSaved(id: number): Promise<void> {
  const opened = st.opened;
  if (!ctx.lib || !opened) return;
  try {
    await request('digitise_delete', { lib: ctx.lib, series_id: id });
    if (st.opened !== opened) return;
    opened.saved = opened.saved.filter((s) => s.id !== id);
    opened.orphans = opened.orphans.filter((s) => s.id !== id);
    if (st.activeSaved === id) newSeries();
    renderAll();
  } catch (e) {
    log('error', `delete: ${rejectionText(e)}`);
  }
}

/** A series kept for review put on the figure shown, by the person looking at both. */
async function attachSaved(id: number): Promise<void> {
  const opened = st.opened;
  if (!ctx.lib || !opened || !st.figure) return;
  try {
    const r = await request<{ series: SavedSeries }>('digitise_attach', { lib: ctx.lib, series_id: id, figure: st.figure });
    if (st.opened !== opened) return;
    opened.orphans = opened.orphans.filter((s) => s.id !== id);
    opened.saved = [...opened.saved, r.series];
    const f = st.list.find((x) => x.figure === st.figure);
    if (f) {
      f.digitised = opened.saved.length;
      renderList();
    }
    renderAll();
  } catch (e) {
    log('error', `review: ${rejectionText(e)}`);
  }
}

/** A library figure's saved series are exported by their ids, as they were saved: the axes on
 *  screen matter only for points on it that are not saved yet, and for an image file's. */
async function exportCsv(): Promise<void> {
  const opened = st.opened;
  if (!opened) return;
  const library = !!(st.figure && ctx.lib);
  const toSave = st.points.length > 0 && (st.dirty || st.activeSaved === null);
  const cal = !library || toSave ? ready('Export') : null;
  if ((!library || toSave) && !cal) return;
  if (library && !toSave && !opened.saved.length) {
    log('error', 'Export: nothing is saved for this figure yet');
    return;
  }
  if (!library && !st.points.length) {
    log('error', 'Export: there are no points to export');
    return;
  }
  const name = $<HTMLInputElement>('dg-name').value.trim() || 'series';
  const src = opened.source;
  const path = await window.litrag.saveAs(exportName(src.paper, src.label, library ? 'digitised' : name));
  if (!path || st.opened !== opened) return;
  try {
    let r: { files: { csv: string; sidecar: string; fits: string[] }[]; points: number };
    if (library) {
      if (toSave) {
        const s = await save();
        if (!s || st.opened !== opened) return;
      }
      r = await request('digitise_export', { lib: ctx.lib, ids: opened.saved.map((s) => s.id), path });
    } else {
      const fits = st.fit ? { [st.fit.model]: st.fit } : {};
      r = await request('digitise_export', { lib: undefined, path, source: { file: src.file ?? null }, series: [{ series: name, colour: st.colour, calibration: cal!.c, points: st.points, method: st.method ?? { mode: 'manual' }, image: opened.image, axes: axesOf(), fits }] });
    }
    const fits = r.files.reduce((n, f) => n + f.fits.length, 0);
    const where = r.files.length === 1 ? `${r.files[0]!.csv}, its sidecar ${r.files[0]!.sidecar}` : `${r.files.length} files, one for each set of axes, each with its sidecar: ${r.files.map((f) => f.csv).join(', ')}`;
    log('info', `Exported ${r.points} points to ${where}${fits ? ` and ${fits} fit${fits === 1 ? '' : 's'}` : ''}`);
  } catch (e) {
    log('error', `export: ${rejectionText(e)}`);
  }
}

/** A saved series fitted, the fit kept with it and shown: the fit an export of it carries. One
 *  that fails leaves none showing. */
async function fitSaved(id: number, model: string): Promise<void> {
  st.fit = null;
  const latest = ticket('fit');
  try {
    const r = await request<{ fits: Record<string, FitResult> }>('fit', { lib: ctx.lib, series_id: id, model });
    if (!latest()) return;
    const s = st.opened?.saved.find((x) => x.id === id);
    if (s) s.fits = { ...s.fits, ...r.fits };
    if (st.activeSaved === id) st.fit = r.fits[model] ?? null;
  } catch (e) {
    log('error', `fit: ${rejectionText(e)}`);
  }
  renderSaved();
  renderFit();
}

async function runFit(): Promise<void> {
  const model = $<HTMLSelectElement>('dg-model').value;
  if (st.figure && ctx.lib && st.activeSaved !== null && !st.dirty) {
    await fitSaved(st.activeSaved, model);
    return;
  }
  const cal = calibration();
  if (!cal) {
    log('error', 'Fit: set the axes first');
    return;
  }
  const latest = ticket('fit');
  try {
    const pts = valued(st.points, cal).map((p) => [p.x, p.y]);
    const r = await request<{ fits: Record<string, FitResult> }>('fit', { points: pts, model });
    if (!latest()) return;
    st.fit = r.fits[model] ?? null;
    renderFit();
  } catch (e) {
    log('error', `fit: ${rejectionText(e)}`);
  }
}

// ---- wiring -------------------------------------------------------------------------------------------

export function initPlots(): void {
  $('plots-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    void find();
  });
  $('dg-open-image').addEventListener('click', () => void openImage());
  for (const b of document.querySelectorAll<HTMLButtonElement>('#dg-tools [data-tool]')) b.addEventListener('click', () => setTool(b.dataset['tool'] as Tool));
  for (const b of document.querySelectorAll<HTMLButtonElement>('.dg-place')) b.addEventListener('click', () => setPlacing(st.placing === b.dataset['point'] ? null : (b.dataset['point'] as Place)));
  for (const input of document.querySelectorAll<HTMLInputElement>('.dg-val')) {
    input.addEventListener('input', () => {
      const key = input.dataset['val']!;
      const axis = key[0] as 'x' | 'y';
      const v = input.value.trim() === '' ? undefined : Number(input.value);
      (st.cal[axis] as Partial<AxisCal>)[key[1] === '1' ? 'v1' : 'v2'] = v;
      st.fromAuto = false;
      st.confirmed = false;
      axesChanged();
    });
  }
  for (const box of document.querySelectorAll<HTMLInputElement>('.dg-log')) {
    box.addEventListener('change', () => {
      st.cal[box.dataset['log'] as 'x' | 'y'].log = box.checked;
      st.fromAuto = false;
      st.confirmed = false;
      axesChanged();
    });
  }
  for (const id of ['dg-xlabel', 'dg-xunit', 'dg-ylabel', 'dg-yunit']) $(id).addEventListener('input', axesChanged);
  $('dg-use-auto').addEventListener('click', () => {
    applyAuto();
    axesChanged();
  });
  $('dg-extract').addEventListener('click', () => void extract());
  $('dg-save').addEventListener('click', () => void save());
  $('dg-new').addEventListener('click', () => {
    newSeries();
    renderAll();
  });
  $('dg-clear').addEventListener('click', () => {
    st.points = [];
    changed();
  });
  $('dg-export').addEventListener('click', () => void exportCsv());
  $('dg-fit').addEventListener('click', () => void runFit());
  $('dg-model').addEventListener('change', () => {
    const s = st.opened?.saved.find((x) => x.id === st.activeSaved);
    st.fit = (!st.dirty && s?.fits[$<HTMLSelectElement>('dg-model').value]) || null;
    renderFit();
  });
  $('dg-zoom-in').addEventListener('click', () => setZoom(st.zoom * 1.25));
  $('dg-zoom-out').addEventListener('click', () => setZoom(st.zoom / 1.25));
  $('dg-zoom-fit').addEventListener('click', zoomFit);
  const stage = $('dg-stage');
  stage.addEventListener('mousedown', onDown);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  window.addEventListener('keydown', (ev) => {
    if (ctx.view !== 'plots' || (ev.target as HTMLElement)?.tagName === 'INPUT') return;
    if (ev.key === 'Escape') setPlacing(null);
  });
  onProjectChange(() => {
    st.list = [];
    st.figure = null;
    st.opened = null;
    st.imagePath = null;
    st.pixels = null;
    reset();
    $('plots-project').textContent = projectName();
    $('plots-total').textContent = '';
    $('dg-canvas-wrap').hidden = true;
    $('dg-empty').hidden = false;
    $('dg-empty').textContent = 'Choose a figure on the left, or open an image.';
    renderList();
    renderAll();
  });
  onViewShown((v) => {
    if (v !== 'plots') return;
    $('plots-project').textContent = projectName();
    if (!st.list.length && ctx.lib) void find();
  });
  hooks.digitise = (figure: string) => {
    showView('plots');
    void openFigure(figure);
  };
  renderAll();
  setTool('colour');
}
