/**
 * The Types tab: the kinds of paper this project holds, each kind's canonical structure (the
 * slots its papers have, in the order they have them, and how often), and — for any one paper
 * — how its printed sections map onto that structure: which slot each section was put in, by
 * which mechanism (the vocabulary, the catalogue, the embedder, a built heading, the outline
 * judge), and which slots the paper does not have. A section the reader could not place is
 * drawn unplaced, never guessed into a slot.
 */

import { $, ctx, el, hooks, log, mechColor, onProjectChange, onViewShown, onWorkerEvent, projectName, request, roleColor } from './shared.ts';

export interface Slot {
  slot: string;
  share?: number;
  median_position?: number | null;
  words_median?: number | null;
  examples?: [string, number][] | { heading: string; count: number }[];
  expected?: boolean;
  status?: string;
  furniture?: boolean;
  canonical?: { name: string; count: number }[] | Record<string, number>;
}

export interface Skeleton {
  type: string;
  papers: number;
  slots: Slot[];
  furniture?: Slot[] | { head?: Slot[]; tail?: Slot[] };
}

export interface MapSection {
  node_id: string;
  heading: string | null;
  level?: number | null;
  role: string;
  canonical?: string | null;
  mechanism: string;
  confidence?: number | null;
  guess?: string | null;
  built?: boolean;
  words?: number;
  paragraphs?: number;
  slot?: string | null;
  children?: MapSection[];
}

export interface Mapping {
  paper: { key: string; title: string; type?: string | null; subtype?: string | null; format?: string | null; confidence?: number | null };
  skeleton?: Skeleton | Slot[];
  sections: MapSection[];
  slots: { slot: string; status: string; sections: string[] }[];
  order?: unknown[];
  unassigned_words_share?: number | null;
}

export interface CanonicalTree {
  slots?: { slot: string; status?: string; sections: { node_id: string; heading: string | null; paragraphs?: number; words?: number; mechanism?: string; moved_from?: string | null }[] }[];
}

const state = {
  overview: [] as { type: string; papers: number; mean_confidence?: number | null; with_methods?: number | null; formats?: Record<string, number> }[],
  skeletons: new Map<string, Skeleton>(),
  type: null as string | null,
  papers: [] as { key: string; title: string; type?: string | null }[],
  paper: null as string | null,
};

export function initTypes(): void {
  onProjectChange(() => {
    state.type = null;
    state.paper = null;
    if (ctx.view === 'types') void loadTypes();
  });
  onViewShown((v) => {
    if (v === 'types') void loadTypes();
  });
  onWorkerEvent((ev) => {
    if (ev['event'] === 'done' && ctx.view === 'types') void loadTypes();
  });
  $<HTMLSelectElement>('mapping-paper').addEventListener('change', (e) => {
    state.paper = (e.target as HTMLSelectElement).value || null;
    void loadMapping();
  });
}

async function loadTypes(): Promise<void> {
  $('types-project').textContent = projectName();
  if (!ctx.lib) return;
  try {
    const r = await request<{ overview: typeof state.overview; skeletons: Skeleton[] | Record<string, Skeleton>; papers?: typeof state.papers }>('types', { lib: ctx.lib });
    state.overview = r.overview ?? [];
    state.skeletons = new Map();
    const list = Array.isArray(r.skeletons) ? r.skeletons : Object.values(r.skeletons ?? {});
    for (const s of list) state.skeletons.set(s.type, s);
    state.papers = r.papers ?? [];
    if (!state.type || !state.skeletons.has(state.type)) state.type = state.overview[0]?.type ?? list[0]?.type ?? null;
  } catch (e) {
    log('error', `types: ${(e as Error).message}`);
    return;
  }
  renderTypeTabs();
  renderSkeleton();
  renderPaperPicker();
  await loadMapping();
}

function renderTypeTabs(): void {
  const box = $('types-list');
  box.innerHTML = '';
  if (!state.overview.length) box.append(el('div', 'empty', 'No papers read yet.'));
  for (const t of state.overview) {
    const tab = el('div', `type-tab${t.type === state.type ? ' on' : ''}`);
    tab.dataset['type'] = t.type;
    tab.append(el('div', 'n', String(t.papers)), el('div', 'l', t.type));
    const extra = [t.mean_confidence !== null && t.mean_confidence !== undefined ? `confidence ${t.mean_confidence.toFixed(2)}` : '', t.formats ? Object.entries(t.formats).map(([f, n]) => `${n} ${f}`).join(' · ') : ''].filter(Boolean).join(' · ');
    if (extra) tab.append(el('div', 'l', extra));
    tab.addEventListener('click', () => {
      state.type = t.type;
      state.paper = null;
      renderTypeTabs();
      renderSkeleton();
      renderPaperPicker();
      void loadMapping();
    });
    box.append(tab);
  }
}

function exampleText(s: Slot): string {
  const ex = s.examples ?? [];
  const pairs = ex.map((e) => (Array.isArray(e) ? e : [e.heading, e.count])) as [string, number][];
  return pairs.slice(0, 4).map(([h, n]) => `“${h}” ×${n}`).join(', ');
}

function renderSkeleton(): void {
  const box = $('skeleton');
  box.innerHTML = '';
  const sk = state.type ? state.skeletons.get(state.type) : undefined;
  $('skeleton-title').textContent = sk ? `Canonical structure · ${sk.type} · ${sk.papers} paper${sk.papers === 1 ? '' : 's'}` : 'Canonical structure';
  if (!sk) return;
  const row = (s: Slot, furniture: boolean) => {
    const r = el('div', `skel-slot${furniture ? ' furniture' : ''}`);
    r.dataset['slot'] = s.slot;
    const sw = el('span', 'sw');
    sw.style.background = roleColor(s.slot.split('/')[0]!);
    const expected = s.expected || s.status === 'expected';
    const nm = el('div', 'nm', s.slot);
    if (expected) nm.title = 'the type’s contract expects this slot';
    const share = el('div', 'share');
    const fill = el('span');
    fill.style.width = `${Math.round(100 * (s.share ?? 0))}%`;
    share.append(fill);
    share.title = `${Math.round(100 * (s.share ?? 0))}% of the type’s papers have it`;
    r.append(sw, nm, share);
    const ex = exampleText(s);
    r.append(el('div', 'ex', [`${Math.round(100 * (s.share ?? 0))}% of papers`, s.words_median ? `~${s.words_median} words` : '', expected ? 'expected by the type' : s.status === 'typical' ? 'typical' : '', ex].filter(Boolean).join(' · ')));
    box.append(r);
  };
  const f = sk.furniture;
  const head = Array.isArray(f) ? [] : f?.head ?? [];
  const tail = Array.isArray(f) ? f : f?.tail ?? [];
  for (const s of head) row(s, true);
  for (const s of sk.slots) row(s, Boolean(s.furniture));
  for (const s of tail) row(s, true);
}

function renderPaperPicker(): void {
  const sel = $<HTMLSelectElement>('mapping-paper');
  sel.innerHTML = '';
  const ofType = state.papers.filter((p) => !state.type || p.type === state.type);
  for (const p of ofType) {
    const o = el('option', undefined, p.title.length > 90 ? `${p.title.slice(0, 90)}…` : p.title) as HTMLOptionElement;
    o.value = p.key;
    sel.append(o);
  }
  if (!state.paper || !ofType.some((p) => p.key === state.paper)) state.paper = ofType[0]?.key ?? null;
  if (state.paper) sel.value = state.paper;
}

async function loadMapping(): Promise<void> {
  const box = $('mapping');
  box.innerHTML = '';
  if (!ctx.lib || !state.paper) return;
  try {
    const r = await request<{ mapping: Mapping }>('mapping', { lib: ctx.lib, key: state.paper });
    renderMapping(box, r.mapping);
  } catch (e) {
    box.append(el('div', 'empty', `No mapping: ${(e as Error).message}`));
  }
}

/** The printed sections on the left, the type's slots on the right, a line from each section to
 *  the slot it was put in, coloured by the mechanism that put it there. */
export function renderMapping(box: HTMLElement, m: Mapping): void {
  box.innerHTML = '';
  const summary = el('div', 'mapping-summary');
  const matched = m.slots.filter((s) => s.status === 'matched').length;
  const missing = m.slots.filter((s) => s.status === 'missing');
  summary.append(el('b', undefined, m.paper.title));
  summary.append(el('span', 'muted', `${m.paper.type ?? '?'}${m.paper.subtype ? '/' + m.paper.subtype : ''} · ${m.paper.format ?? ''}`));
  summary.append(el('span', undefined, `${matched} of ${m.slots.filter((s) => s.status === 'matched' || s.status === 'missing').length} slots matched`));
  if (missing.length) summary.append(el('span', undefined, `missing: ${missing.map((s) => s.slot).join(', ')}`));
  if (m.unassigned_words_share) summary.append(el('span', undefined, `${Math.round(100 * m.unassigned_words_share)}% of words unplaced`));
  const open = el('button', 'ghost small', 'Open in Papers');
  open.addEventListener('click', () => hooks.openPaper(m.paper.key));
  summary.append(open);
  box.append(summary);

  const legend = el('div', 'legend');
  legend.style.padding = '6px 12px';
  for (const mech of ['vocabulary', 'catalogue', 'embedder', 'built', 'outline', 'other']) {
    const item = el('span');
    const sw = el('i');
    sw.style.background = mechColor(mech);
    item.append(sw, document.createTextNode(mech === 'other' ? 'unplaced' : mech));
    legend.append(item);
  }
  box.append(legend);

  const map = el('div', 'map');
  const left = el('div', 'map-col');
  const right = el('div', 'map-col');
  left.append(el('div', 'map-h', 'As printed'));
  map.append(left, el('div'), right);
  right.append(el('div', 'map-h', 'Canonical slots'));
  const slotEls = new Map<string, HTMLElement>();
  for (const s of m.slots) {
    if (s.status === 'absent') continue; // a slot the type rarely has and this paper does not: nothing to draw
    const d = el('div', `map-slot${s.status === 'missing' ? ' missing' : ''}`, s.slot + (s.status === 'missing' ? ' — expected, not in this paper' : s.status === 'extra' ? ' (not usual for the type)' : ''));
    d.dataset['slot'] = s.slot;
    d.style.borderLeftColor = roleColor(s.slot.split('/')[0]!);
    right.append(d);
    slotEls.set(s.slot, d);
  }
  const secEls: [HTMLElement, MapSection][] = [];
  for (const s of m.sections) {
    const d = el('div', 'map-sec');
    d.dataset['id'] = s.node_id;
    d.style.borderLeftColor = mechColor(s.mechanism);
    d.append(el('div', undefined, (s.heading ?? '(untitled)') + (s.built ? ' [built]' : '')));
    const sub = [s.role, s.canonical && s.canonical !== s.heading ? `“${s.canonical}”` : '', `by ${s.mechanism}`, s.confidence !== null && s.confidence !== undefined ? `conf ${s.confidence.toFixed(2)}` : '', s.guess && s.role === 'other' ? `nearly ${s.guess}` : '', s.words ? `${s.words} words` : '', s.children?.length ? `${s.children.length} subsections` : ''].filter(Boolean).join(' · ');
    d.append(el('div', 'sub', sub));
    d.title = 'Open this section in the paper';
    d.addEventListener('click', () => hooks.openPaper(m.paper.key, s.node_id));
    left.append(d);
    secEls.push([d, s]);
  }
  box.append(map);
  // the lines, once the boxes are laid out
  requestAnimationFrame(() => {
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    const base = map.getBoundingClientRect();
    svg.setAttribute('width', String(base.width));
    svg.setAttribute('height', String(base.height));
    for (const [d, s] of secEls) {
      const slot = s.slot ?? m.slots.find((x) => x.sections.includes(s.node_id))?.slot;
      const target = slot ? slotEls.get(slot) : undefined;
      if (!target) continue;
      const a = d.getBoundingClientRect();
      const b = target.getBoundingClientRect();
      const x1 = a.right - base.left;
      const y1 = a.top + a.height / 2 - base.top;
      const x2 = b.left - base.left;
      const y2 = b.top + b.height / 2 - base.top;
      const path = document.createElementNS(svgNS, 'path');
      const mx = (x1 + x2) / 2;
      path.setAttribute('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
      path.setAttribute('fill', 'none');
      path.style.stroke = mechColor(s.mechanism);
      path.setAttribute('stroke-width', '2');
      path.setAttribute('opacity', '.8');
      path.dataset['from'] = s.node_id;
      path.dataset['to'] = slot!;
      svg.append(path);
    }
    map.append(svg);
  });
}

/** The paper re-hung under its type's structure, for the Papers view's "Canonical" face: front
 *  matter, the type's slots in order (a slot the type expects and the paper lacks drawn empty),
 *  the paper's own extra slots, what maps nowhere, then references and back matter. */
export function renderCanonicalTree(box: HTMLElement, t: CanonicalTree, onSection: (id: string) => void): void {
  box.innerHTML = '';
  for (const s of t.slots ?? []) {
    if (s.status === 'absent') continue;
    const slot = el('div', `slot${s.status === 'missing' ? ' missing' : ''}`);
    slot.dataset['slot'] = s.slot;
    const head = el('div', 'slot-head');
    const sw = el('span', 'sw');
    sw.style.background = roleColor(s.slot.split('/')[0]!);
    head.append(sw, el('span', undefined, s.slot));
    if (s.status === 'missing') head.append(el('span', 'muted', '— the type expects this; this paper does not have it'));
    if (s.status === 'extra') head.append(el('span', 'muted', '— not usual for the type'));
    slot.append(head);
    for (const sec of s.sections) {
      const row = el('div', 'slot-sec');
      row.dataset['id'] = sec.node_id;
      const mech = el('span', 'mech', sec.mechanism ?? '?');
      mech.style.background = mechColor(sec.mechanism ?? 'unknown');
      row.append(mech, el('span', undefined, (sec.heading ?? '(untitled)') + (sec.moved_from ? ` (moved from ${sec.moved_from})` : '')));
      row.append(el('span', 'n', [sec.paragraphs ? `${sec.paragraphs} ¶` : '', sec.words ? `${sec.words} w` : ''].filter(Boolean).join(' · ')));
      row.addEventListener('click', () => onSection(sec.node_id));
      slot.append(row);
    }
    box.append(slot);
  }
}
