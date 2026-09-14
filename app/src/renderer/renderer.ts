/**
 * The window: papers on the left as they are ingested, the tree of the chosen
 * paper in the middle, and on the right the page the chosen node came from
 * with its box drawn on it. Everything shown here is a row the worker
 * returned; the renderer holds no state of its own beyond the selection.
 */

import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { LitragApi } from '../main/preload.ts';

declare global {
  interface Window {
    litrag: LitragApi;
  }
}

pdfjs.GlobalWorkerOptions.workerSrc = new URL('./pdf.worker.min.mjs', import.meta.url).href;

// ---- shapes the worker returns ------------------------------------------------------

interface Paper {
  key: string;
  title: string;
  doi: string | null;
  file: string | null;
  format: string | null;
  pages: number | null;
  status: string;
  error: string | null;
  nodes: number;
  has_methods: number | null;
  seconds: number | null;
  /** what kind of paper, and who said so (paper_type.py) */
  type?: string | null;
  subtype?: string | null;
  type_source?: string | null;
  /** who wrote it, where and when: the JATS file's word, else Europe PMC's record (record.py); authors is JSON */
  authors?: string | null;
  journal?: string | null;
  year?: string | null;
  // live, from events
  stage?: string;
  message?: string;
  elapsed?: number;
  roles?: Record<string, number>;
}

interface Node {
  /** the catalogue's name for a section (headings.py), null when it has none */
  canonical?: string | null;
  node_id: string;
  parent: string | null;
  ordinal: number;
  depth: number;
  type: string;
  label: string;
  level: number | null;
  role: string;
  heading: string | null;
  ancestry: string[];
  text: string;
  page: number | null;
  bbox: [number, number, number, number] | null;
  self_ref: string | null;
  table: { rows: number; cols: number; cells: string[][] } | null;
  children: Node[];
  /** reference numbers this node cites, from the `citations` rows */
  cites?: number[];
  /** for an entry in the reference list: its number */
  ref_no?: number;
}

/** one edge of a node, with the node at the other end (store.edges_of) */
interface EdgeRow {
  kind: string;
  evidence: string;
  detail: string | null;
  score: number | null;
  node_id: string;
  type: string;
  role: string;
  heading: string | null;
  ancestry: string[];
  page: number | null;
  text: string;
}

interface Ref {
  ref_no: number;
  node_id: string | null;
  ref_id: string | null;
  text: string;
  doi: string | null;
  pmid: string | null;
  year: string | null;
  first_author: string | null;
  title: string | null;
  cited_by: string[];
}

interface Tree {
  paper: Paper;
  pages: { page_no: number; width: number; height: number }[];
  roles: Record<string, number>;
  root: Node;
}

const ROLES = ['abstract', 'introduction', 'methods', 'results', 'results-discussion', 'discussion', 'other', 'references', 'back'];
const roleColor = (role: string) => `var(--${ROLES.includes(role) ? role : 'other'})`;

// ---- state ------------------------------------------------------------------------------

const state = {
  libraries: [] as { id: string; name: string }[],
  lib: null as string | null,
  papers: new Map<string, Paper>(),
  selectedPaper: null as string | null,
  tree: null as Tree | null,
  nodesById: new Map<string, Node>(),
  refs: new Map<number, Ref>(),
  selectedNode: null as string | null,
  roleFilter: null as string | null,
  pdf: null as PDFDocumentProxy | null,
  pdfKey: null as string | null,
  page: 1,
  collapsed: new Set<string>(),
};

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const el = (tag: string, cls?: string, text?: string) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

// ---- worker status & log --------------------------------------------------------------

function setStatus(kind: 'ok' | 'busy' | 'bad' | '', text: string) {
  const s = $('worker-status');
  s.className = `status ${kind}`;
  s.querySelector('.text')!.textContent = text;
}

function log(kind: string, text: string, paper?: string) {
  const line = el('div', `log-line ${kind}`);
  line.append(el('span', 't', new Date().toLocaleTimeString()));
  if (paper) line.append(el('span', 'paper', paper));
  line.append(el('span', 'm', text));
  const box = $('log');
  box.append(line);
  while (box.childElementCount > 400) box.firstElementChild?.remove();
  box.scrollTop = box.scrollHeight;
}

// ---- libraries --------------------------------------------------------------------------

let librariesLoading: Promise<void> | null = null;
async function loadLibraries(): Promise<void> {
  if (librariesLoading) return librariesLoading;
  librariesLoading = loadLibrariesOnce().finally(() => (librariesLoading = null));
  return librariesLoading;
}

async function loadLibrariesOnce() {
  let r: { libraries: { id: string; name: string }[]; root: string };
  try {
    r = (await window.litrag.request('libraries')) as unknown as typeof r;
  } catch (e) {
    log('error', `libraries: ${(e as Error).message}`);
    return;
  }
  state.libraries = r.libraries;
  const sel = $<HTMLSelectElement>('library');
  sel.innerHTML = '';
  for (const l of r.libraries) {
    const o = el('option', undefined, l.name) as HTMLOptionElement;
    o.value = l.id;
    sel.append(o);
  }
  if (!r.libraries.length) {
    const o = el('option', undefined, 'No libraries yet') as HTMLOptionElement;
    o.value = '';
    sel.append(o);
    state.lib = null;
    renderPapers();
    return;
  }
  if (!state.lib || !r.libraries.some((l) => l.id === state.lib)) state.lib = r.libraries[0]!.id;
  sel.value = state.lib;
  await loadPapers();
}

async function newLibrary() {
  const name = window.prompt('Name of the new library (one per project):');
  if (!name?.trim()) return;
  try {
    const r = (await window.litrag.request('init', { name: name.trim() })) as unknown as { library: { id: string } };
    state.lib = r.library.id;
    log('stage', `Created library ${r.library.id}`);
    await loadLibraries();
  } catch (e) {
    log('error', String((e as Error).message));
  }
}

// ---- papers -----------------------------------------------------------------------------

async function loadPapers() {
  if (!state.lib) return;
  const r = (await window.litrag.request('papers', { lib: state.lib })) as unknown as { papers: Paper[] };
  const live = state.papers;
  state.papers = new Map();
  for (const p of r.papers) {
    const prev = live.get(p.key);
    state.papers.set(p.key, { ...p, stage: prev?.stage, message: prev?.message, elapsed: prev?.elapsed, roles: prev?.roles });
  }
  renderPapers();
  if (state.selectedPaper && state.papers.has(state.selectedPaper)) await loadTree(state.selectedPaper);
}

/** "Trung DD, Duong PV, Hoa NM et al. · RSC Adv · 2026" — what the record says, nothing when it says nothing */
function byline(p: Paper, names: number): string {
  let authors: string[] = [];
  try {
    const parsed = p.authors ? (JSON.parse(p.authors) as { name?: string }[]) : [];
    authors = parsed.map((a) => a.name ?? '').filter(Boolean);
  } catch {
    authors = [];
  }
  const who = authors.length ? authors.slice(0, names).join(', ') + (authors.length > names ? ' et al.' : '') : '';
  return [who, p.journal ?? '', p.year ?? ''].filter(Boolean).join(' · ');
}

function roleBar(roles: Record<string, number> | undefined): HTMLElement {
  const bar = el('div', 'bar');
  if (!roles) return bar;
  const total = Object.entries(roles).filter(([r]) => r !== 'references' && r !== 'back').reduce((a, [, n]) => a + n, 0) || 1;
  for (const role of ROLES) {
    const n = roles[role];
    if (!n || role === 'references' || role === 'back') continue;
    const seg = el('span');
    seg.style.width = `${(100 * n) / total}%`;
    seg.style.background = roleColor(role);
    seg.title = `${role}: ${n}`;
    bar.append(seg);
  }
  return bar;
}

function renderPapers() {
  const box = $('papers');
  box.innerHTML = '';
  const papers = [...state.papers.values()];
  $('papers-count').textContent = papers.length ? `${papers.length}` : '';
  if (!state.lib) {
    box.append(el('div', 'empty', 'Create a library to start.'));
    return;
  }
  if (!papers.length) {
    box.append(el('div', 'empty', 'No papers yet. Add PDFs or drop them here.'));
    return;
  }
  for (const p of papers) {
    const card = el('div', `paper${p.key === state.selectedPaper ? ' selected' : ''}`);
    card.dataset['key'] = p.key;
    card.append(el('div', 'title', p.title || p.file || p.key));
    card.append(el('div', 'key', [p.key, p.pages ? `${p.pages} pp` : '', p.format ?? '', p.type && p.type !== 'other' ? `${p.type}${p.subtype ? '/' + p.subtype : ''} (by ${p.type_source ?? '?'})` : ''].filter(Boolean).join(' · ')));
    const by = byline(p, 3);
    if (by) card.append(el('div', 'byline', by));
    const stage = el('div', 'stage');
    stage.append(el('span', `badge ${p.status}`, p.status));
    if (p.status === 'parsing') stage.append(el('span', 'muted', `${p.stage ?? ''}${p.elapsed !== undefined ? ` · ${p.elapsed.toFixed(0)}s` : ''}`));
    else if (p.status === 'parsed') stage.append(el('span', 'muted', `${p.nodes} nodes${p.seconds ? ` · ${p.seconds}s` : ''}`));
    else if (p.status === 'failed') stage.append(el('span', 'muted', p.error ?? p.message ?? ''));
    card.append(stage);
    if (p.status === 'parsing') card.append(el('div', 'progress'));
    if (p.status === 'parsed' && p.roles) card.append(roleBar(p.roles));
    if (p.status === 'parsed' && p.has_methods === 0) card.append(el('div', 'flag', 'No methods section detected'));
    card.addEventListener('click', () => void selectPaper(p.key));
    box.append(card);
  }
}

// ---- tree -------------------------------------------------------------------------------

async function selectPaper(key: string) {
  state.selectedPaper = key;
  state.selectedNode = null;
  state.roleFilter = null;
  state.collapsed = new Set();
  renderPapers();
  await loadTree(key);
}

let treeLoading: { key: string; promise: Promise<void> } | null = null;
async function loadTree(key: string): Promise<void> {
  if (treeLoading?.key === key) return treeLoading.promise;
  const promise = loadTreeOnce(key).finally(() => {
    if (treeLoading?.promise === promise) treeLoading = null;
  });
  treeLoading = { key, promise };
  return promise;
}

async function loadTreeOnce(key: string) {
  const p = state.papers.get(key);
  if (!p || p.status !== 'parsed') {
    state.tree = null;
    renderTree();
    return;
  }
  try {
    const r = (await window.litrag.request('tree', { lib: state.lib, key })) as unknown as Tree;
    state.tree = r;
    state.nodesById = new Map();
    const walk = (n: Node) => {
      state.nodesById.set(n.node_id, n);
      n.children.forEach(walk);
    };
    walk(r.root);
    // the reference list arrives after the tree; the old one is kept until the new one is here, and a
    // node selected in the meantime has its detail drawn again once the links can be drawn
    try {
      const rr = (await window.litrag.request('refs', { lib: state.lib, key })) as unknown as { refs: Ref[] };
      const refs = new Map<number, Ref>();
      for (const ref of rr.refs) refs.set(ref.ref_no, ref);
      state.refs = refs;
    } catch (e) {
      state.refs = new Map();
      log('error', `Could not load references: ${(e as Error).message}`, key);
    }
    if (state.selectedNode) {
      const selected = state.nodesById.get(state.selectedNode);
      if (selected) renderDetail(selected);
    }
    // start with references and back matter folded: they are long and rarely the point
    for (const c of r.root.children) if (c.role === 'references' || c.role === 'back') state.collapsed.add(c.node_id);
    renderTree();
    await openPdf(key);
    const first = r.root.children.find((c) => c.type === 'section');
    if (first?.page) await showPage(first.page);
  } catch (e) {
    log('error', String((e as Error).message), key);
  }
}

function renderTree() {
  const t = state.tree;
  const title = $('tree-title');
  const summary = $('tree-summary');
  const box = $('tree');
  const chips = $('role-filter');
  box.innerHTML = '';
  summary.innerHTML = '';
  chips.innerHTML = '';
  if (!t) {
    title.textContent = 'Tree';
    const p = state.selectedPaper ? state.papers.get(state.selectedPaper) : null;
    box.append(el('div', 'empty', p ? (p.status === 'failed' ? `Parsing failed: ${p.error ?? ''}` : 'Not parsed yet — the tree appears when the worker has saved it.') : 'Pick a paper.'));
    return;
  }
  title.textContent = t.paper.title;
  summary.append(el('span', undefined, `${t.pages.length} pages`));
  summary.append(el('span', undefined, `${Object.values(t.roles).reduce((a, b) => a + b, 0)} nodes`));
  summary.append(el('span', undefined, t.paper.has_methods ? 'methods section found' : 'no methods section'));
  if (t.paper.seconds) summary.append(el('span', undefined, `parsed in ${t.paper.seconds}s`));
  const by = byline(t.paper, 8);
  if (by) summary.append(el('span', 'byline', by));
  for (const role of ROLES) {
    const n = t.roles[role];
    if (!n) continue;
    const chip = el('span', `chip${state.roleFilter === role ? ' on' : ''}`, `${role} ${n}`);
    if (state.roleFilter === role) chip.style.background = roleColor(role);
    chip.addEventListener('click', () => {
      state.roleFilter = state.roleFilter === role ? null : role;
      renderTree();
    });
    chips.append(chip);
  }
  for (const c of t.root.children) box.append(renderNode(c));
}

function nodeLabel(n: Node): string {
  if (n.type === 'section') return (n.heading ?? '(untitled)') + (n.label === 'built' ? ' [built]' : '') + (n.canonical && n.canonical.toLowerCase() !== (n.heading ?? '').replace(/^[\d.\s]+/, '').toLowerCase() ? ` [${n.canonical}]` : '');
  if (n.type === 'table') return n.table ? `table ${n.table.rows}×${n.table.cols}` : 'table';
  if (n.type === 'picture') return 'figure';
  const t = n.text.replace(/\s+/g, ' ').trim();
  return t.length > 110 ? `${t.slice(0, 110)}…` : t || `(${n.label})`;
}

function renderNode(n: Node): HTMLElement {
  const wrap = el('div');
  const row = el('div', `tree-node ${n.type}${n.node_id === state.selectedNode ? ' selected' : ''}${state.roleFilter && n.role !== state.roleFilter ? ' dim' : ''}`);
  row.dataset['id'] = n.node_id;
  const hasKids = n.children.length > 0;
  const collapsed = state.collapsed.has(n.node_id);
  const twisty = el('span', 'twisty', hasKids ? (collapsed ? '▸' : '▾') : '');
  twisty.addEventListener('click', (e) => {
    e.stopPropagation();
    if (collapsed) state.collapsed.delete(n.node_id);
    else state.collapsed.add(n.node_id);
    renderTree();
  });
  row.append(twisty);
  const dot = el('span', 'role');
  dot.style.background = roleColor(n.role);
  dot.title = n.role;
  row.append(dot);
  if (n.type !== 'section' && n.type !== 'paragraph') row.append(el('span', 'kind', n.type));
  row.append(el('span', 'label', nodeLabel(n)));
  if (n.type === 'section') row.append(el('span', 'count', `${countLeaves(n)}`));
  if (n.cites?.length) {
    const c = el('span', 'cites', `→ ${n.cites.length}`);
    c.title = `cites ${n.cites.map((r) => `[${r}]`).join(' ')}`;
    row.append(c);
  }
  if (n.ref_no) {
    const cited = state.refs.get(n.ref_no)?.cited_by.length ?? 0;
    const c = el('span', 'cites', cited ? `[${n.ref_no}] ← ${cited}` : `[${n.ref_no}]`);
    c.title = cited ? `cited by ${cited} node${cited === 1 ? '' : 's'}` : 'never cited in the text';
    row.append(c);
  }
  if (n.page) row.append(el('span', 'pg', `p.${n.page}`));
  row.addEventListener('click', () => void selectNode(n.node_id));
  wrap.append(row);
  if (hasKids && !collapsed) {
    const kids = el('div', 'children');
    for (const c of n.children) kids.append(renderNode(c));
    wrap.append(kids);
  }
  return wrap;
}

function countLeaves(n: Node): number {
  return n.children.reduce((a, c) => a + (c.type === 'section' ? countLeaves(c) : 1), 0);
}

// ---- node detail & page -------------------------------------------------------------------

async function selectNode(id: string) {
  state.selectedNode = id;
  const n = state.nodesById.get(id);
  // a node inside a folded section — an entry in the references, reached from a citation — is unfolded to be seen
  for (let p = n?.parent ? state.nodesById.get(n.parent) : undefined; p; p = p.parent ? state.nodesById.get(p.parent) : undefined) state.collapsed.delete(p.node_id);
  renderTree();
  document.querySelector<HTMLElement>('#tree .tree-node.selected')?.scrollIntoView({ block: 'nearest' });
  if (!n) return;
  renderDetail(n);
  if (n.page) await showPage(n.page);
  else {
    drawBoxes();
    highlightReading(id);
  }
}

// ---- an XML paper, read back from its tree --------------------------------------------------

/** Headings, paragraphs, formulas, tables, figures as placeholders — every one a node a click selects. */
function renderReading() {
  const box = $('page-reading');
  box.innerHTML = '';
  const t = state.tree;
  if (!t) return;
  $('page-stage').hidden = true;
  box.hidden = false;
  box.append(el('h1', 'rv-title', t.paper.title));
  const walk = (n: Node, into: HTMLElement) => {
    let list: HTMLElement | null = null;
    for (const c of n.children) {
      if (c.type === 'list_item') {
        if (!list) {
          list = el('ul');
          into.append(list);
        }
        list.append(readingNode(c, 'li'));
        continue;
      }
      list = null;
      if (c.type === 'section') {
        const sec = el('section');
        const h = el(`h${Math.min(2 + Math.max(0, c.depth - 1), 4)}`, 'rv section', (c.heading ?? '(untitled)') + (c.label === 'built' ? ' [built]' : '') + (c.canonical && c.canonical.toLowerCase() !== (c.heading ?? '').replace(/^[\d.\s]+/, '').toLowerCase() ? ` [${c.canonical}]` : ''));
        wireReading(h, c);
        sec.append(h);
        walk(c, sec);
        into.append(sec);
      } else if (c.type === 'table' || c.type === 'picture') {
        const fig = el('figure', `rv ${c.type}`);
        wireReading(fig, c);
        if (c.type === 'table') {
          const table = el('table');
          for (const row of c.table?.cells ?? []) {
            const tr = el('tr');
            for (const cell of row) tr.append(el('td', undefined, cell));
            table.append(tr);
          }
          fig.append(table);
        } else {
          fig.append(el('div', 'placeholder', 'figure — the image itself is not in the XML'));
        }
        for (const cap of c.children) if (cap.type === 'caption') fig.append(el('figcaption', undefined, cap.text));
        into.append(fig);
      } else if (c.type === 'formula') {
        into.append(readingNode(c, 'pre'));
      } else {
        into.append(readingNode(c, 'p'));
      }
    }
  };
  walk(t.root, box);
  if (state.selectedNode) highlightReading(state.selectedNode);
}

function readingNode(n: Node, tag: string): HTMLElement {
  const e = el(tag, `rv ${n.type}`, n.text);
  wireReading(e, n);
  return e;
}

function wireReading(e: HTMLElement, n: Node) {
  e.dataset['id'] = n.node_id;
  e.style.borderLeftColor = roleColor(n.role);
  e.title = n.role;
  if (n.node_id === state.selectedNode) e.classList.add('selected');
  e.addEventListener('click', (ev) => {
    ev.stopPropagation();
    void selectNode(n.node_id);
  });
}

function highlightReading(id: string) {
  const box = $('page-reading');
  if (box.hidden) return;
  for (const e of box.querySelectorAll('.rv.selected')) e.classList.remove('selected');
  const target = box.querySelector<HTMLElement>(`.rv[data-id="${CSS.escape(id)}"]`);
  if (target) {
    target.classList.add('selected');
    target.scrollIntoView({ block: 'center' });
  }
}

function renderDetail(n: Node) {
  const d = $('node-detail');
  d.innerHTML = '';
  const crumbs = el('div', 'crumbs');
  crumbs.innerHTML = [...n.ancestry, n.type === 'section' ? n.heading ?? '' : ''].filter(Boolean).map((s) => `<b>${escapeHtml(s)}</b>`).join(' › ') || '<span>top level</span>';
  d.append(crumbs);
  const meta = el('div', 'meta');
  const roleTag = el('span', undefined, n.role);
  roleTag.style.color = roleColor(n.role);
  roleTag.style.fontWeight = '600';
  meta.append(roleTag, el('span', undefined, n.type), el('span', undefined, `docling: ${n.label}`));
  if (n.page) meta.append(el('span', undefined, `page ${n.page}`));
  if (n.bbox) meta.append(el('span', undefined, `box ${n.bbox.map((v) => v.toFixed(0)).join(', ')} pt`));
  meta.append(el('span', undefined, n.node_id));
  d.append(meta);
  if (n.table) {
    const table = el('table');
    for (const row of n.table.cells) {
      const tr = el('tr');
      for (const cell of row) tr.append(el('td', undefined, cell));
      table.append(tr);
    }
    d.append(table);
  }
  if (n.text) d.append(el('div', 'text', n.text));
  if (n.cites?.length) {
    const box = el('div', 'links');
    box.append(el('div', 'links-head', `Cites ${n.cites.length} ${n.cites.length === 1 ? 'entry' : 'entries'} of the reference list`));
    for (const no of n.cites) {
      const ref = state.refs.get(no);
      const row = el('div', 'link');
      const tag = el('span', 'tag', `[${no}]`);
      row.append(tag);
      row.append(el('span', 'who', ref ? `${ref.first_author ?? '?'} ${ref.year ?? ''}`.trim() : ''));
      row.append(el('span', 'what', ref ? (ref.title ?? ref.text) : '(entry not found)'));
      if (ref?.doi) row.append(el('span', 'doi', ref.doi));
      if (ref?.node_id) {
        row.classList.add('go');
        row.title = 'open the entry';
        row.addEventListener('click', () => void selectNode(ref.node_id!));
      }
      box.append(row);
    }
    d.append(box);
  }
  if (n.ref_no) {
    const ref = state.refs.get(n.ref_no);
    const box = el('div', 'links');
    const citing = ref?.cited_by ?? [];
    box.append(el('div', 'links-head', citing.length ? `Entry [${n.ref_no}] — cited by ${citing.length} node${citing.length === 1 ? '' : 's'}` : `Entry [${n.ref_no}] — never cited in the text`));
    if (ref?.doi) box.append(el('div', 'doi', `doi:${ref.doi}${ref.pmid ? ` · pmid:${ref.pmid}` : ''}`));
    for (const id of citing) {
      const citer = state.nodesById.get(id);
      const row = el('div', 'link go');
      row.append(el('span', 'tag', citer?.role ?? ''));
      row.append(el('span', 'what', citer ? `${citer.ancestry.join(' › ')} — ${citer.text.slice(0, 140)}` : id));
      row.title = 'open the citing node';
      row.addEventListener('click', () => void selectNode(id));
      box.append(row);
    }
    d.append(box);
  }
  if (n.type === 'section' && !n.text) d.append(el('div', 'muted', `${n.children.length} children`));
  void loadEdges(n, d);
}

/** The edges of the selected node, drawn once they arrive: the methods a finding was measured by,
 *  the findings measured under a method, the figures a paragraph cites and the paragraphs citing a figure. */
let edgesSeq = 0;
async function loadEdges(n: Node, into: HTMLElement) {
  if (!state.lib) return;
  const seq = ++edgesSeq;
  let r: { out: EdgeRow[]; in: EdgeRow[]; candidates: number };
  try {
    r = (await window.litrag.request('edges', { lib: state.lib, node_id: n.node_id })) as unknown as { out: EdgeRow[]; in: EdgeRow[]; candidates: number };
  } catch {
    return;
  }
  if (state.selectedNode !== n.node_id || seq !== edgesSeq) return; // another node, or the same one again, was chosen while this was on its way
  for (const old of into.querySelectorAll('.links.edges')) old.remove();
  const measuredBy = r.out.filter((e) => e.kind === 'measured_by');
  const groups: [string, EdgeRow[]][] = [
    ['Measured by', measuredBy],
    ['Findings measured here', r.in.filter((e) => e.kind === 'measured_by')],
    ['Cites', r.out.filter((e) => e.kind === 'cites_figure')],
    ['Cited by', r.in.filter((e) => e.kind === 'cites_figure')],
  ];
  // the same test edges.py applies: a results paragraph, or a discussion paragraph that cites a figure, of eight words or more
  const words = n.text.split(/\s+/).filter(Boolean).length;
  const isFinding = n.type === 'paragraph' && words >= 8 && (n.role === 'results' || n.role === 'results-discussion' || (n.role === 'discussion' && /\b(fig(ure)?s?|tables?|schemes?)\.?\s*S?\d/i.test(n.text)));
  if (isFinding && !measuredBy.length) {
    const why = r.candidates === 0 ? 'the paper has no methods section to link to' : r.candidates === 1 ? 'the methods have one part, which only a pointer could name' : 'no pointer, no term only one method owns, nothing in a cited caption';
    const box = el('div', 'links edges');
    box.append(el('div', 'links-head', `Measured by — no method found: ${why}`));
    into.append(box);
  }
  for (const [title, rows] of groups) {
    if (!rows.length) continue;
    const box = el('div', 'links edges');
    box.append(el('div', 'links-head', `${title} (${rows.length})`));
    for (const e of rows) {
      const row = el('div', 'link go');
      const tag = el('span', 'tag', e.evidence);
      tag.title = e.detail ?? '';
      row.append(tag);
      const where = e.type === 'section' ? (e.heading ?? '(untitled)') : [...e.ancestry].slice(-1).join('');
      row.append(el('span', 'who', where));
      row.append(el('span', 'what', e.type === 'section' || e.kind === 'cites_figure' ? (e.detail ?? '') : e.text.slice(0, 140)));
      row.title = e.detail ? `${e.evidence}: ${e.detail}` : e.evidence;
      row.addEventListener('click', () => void selectNode(e.node_id));
      box.append(row);
    }
    into.append(box);
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}

let pdfOpening: { key: string; promise: Promise<void> } | null = null;
async function openPdf(key: string): Promise<void> {
  if (state.pdfKey === key && state.pdf) return;
  if (pdfOpening?.key === key) return pdfOpening.promise;
  const promise = openPdfOnce(key).finally(() => {
    if (pdfOpening?.promise === promise) pdfOpening = null;
  });
  pdfOpening = { key, promise };
  return promise;
}

async function openPdfOnce(key: string) {
  void state.pdf?.destroy();
  state.pdf = null;
  state.pdfKey = key;
  const p = state.papers.get(key);
  if (!p || p.format !== 'pdf') {
    clearPage();
    if (p?.format === 'jats' && state.tree) {
      // no page to draw: the tree is the paper, so show it as one
      $('page-label').textContent = 'XML · the paper as read';
      renderReading();
    } else {
      $('page-label').textContent = p?.format === 'jats' ? 'XML: no pages' : '–';
      $('page-reading').hidden = true;
      $('page-stage').hidden = false;
    }
    return;
  }
  $('page-reading').hidden = true;
  $('page-stage').hidden = false;
  try {
    const f = (await window.litrag.request('file', { lib: state.lib, key })) as unknown as { path: string };
    const bytes = await window.litrag.readFile(f.path);
    state.pdf = await pdfjs.getDocument({ data: new Uint8Array(bytes) }).promise;
  } catch (e) {
    log('error', `Could not open PDF: ${(e as Error).message}`, key);
  }
}

function clearPage() {
  const canvas = $<HTMLCanvasElement>('page-canvas');
  canvas.width = 0;
  canvas.height = 0;
  $('page-overlay').innerHTML = '';
}

let rendering: Promise<void> | null = null;
async function showPage(pageNo: number) {
  // a node clicked while its PDF is still opening waits for it rather than showing nothing
  if (!state.pdf && pdfOpening) await pdfOpening.promise;
  if (!state.pdf) return;
  state.page = Math.min(Math.max(1, pageNo), state.pdf.numPages);
  $('page-label').textContent = `${state.page} / ${state.pdf.numPages}`;
  const run = async () => {
    const page = await state.pdf!.getPage(state.page);
    const wrapWidth = $('page-wrap').clientWidth - 24;
    const base = page.getViewport({ scale: 1 });
    const scale = Math.max(0.5, Math.min(2, wrapWidth / base.width));
    const viewport = page.getViewport({ scale });
    const canvas = $<HTMLCanvasElement>('page-canvas');
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    const ctx = canvas.getContext('2d')!;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    await page.render({ canvasContext: ctx, viewport, canvas }).promise;
    drawBoxes();
  };
  rendering = (rendering ?? Promise.resolve()).then(run, run);
  await rendering;
}

/** Every node on the page as a faint box, the selected one bold; Docling's points scaled to the canvas. */
function drawBoxes() {
  const overlay = $('page-overlay');
  overlay.innerHTML = '';
  const t = state.tree;
  if (!t || !state.pdf) return;
  const pageMeta = t.pages.find((p) => p.page_no === state.page);
  const canvas = $<HTMLCanvasElement>('page-canvas');
  const w = parseFloat(canvas.style.width) || 0;
  const h = parseFloat(canvas.style.height) || 0;
  if (!pageMeta || !w) return;
  const sx = w / pageMeta.width;
  const sy = h / pageMeta.height;
  for (const n of state.nodesById.values()) {
    if (n.page !== state.page || !n.bbox) continue;
    if (state.roleFilter && n.role !== state.roleFilter && n.node_id !== state.selectedNode) continue;
    const [l, top, r, b] = n.bbox;
    const box = el('div', `box${n.node_id === state.selectedNode ? '' : ' faint'}`);
    box.style.left = `${l * sx}px`;
    box.style.top = `${top * sy}px`;
    box.style.width = `${(r - l) * sx}px`;
    box.style.height = `${(b - top) * sy}px`;
    box.style.borderColor = roleColor(n.role);
    if (n.node_id === state.selectedNode) box.style.background = 'rgba(47,93,138,.14)';
    box.title = `${n.role} · ${n.type}`;
    box.style.pointerEvents = 'auto';
    box.addEventListener('click', () => void selectNode(n.node_id));
    overlay.append(box);
    if (n.node_id === state.selectedNode) box.scrollIntoView({ block: 'center' });
  }
}

// ---- ingest -------------------------------------------------------------------------------

async function ingest(paths: string[]) {
  if (!state.lib) {
    log('error', 'Create a library first.');
    return;
  }
  if (!paths.length) return;
  log('stage', `Ingesting ${paths.length} file${paths.length === 1 ? '' : 's'}`);
  try {
    await window.litrag.request('ingest', { lib: state.lib, paths });
  } catch (e) {
    log('error', String((e as Error).message));
  }
}

// ---- events from the worker ------------------------------------------------------------------

function onEvent(ev: Record<string, unknown>) {
  const kind = String(ev['event']);
  const paper = typeof ev['paper'] === 'string' ? ev['paper'] : undefined;
  switch (kind) {
    case 'ready':
      setStatus('ok', `worker ready · ${ev['root']}`);
      void loadLibraries();
      break;
    case 'paper': {
      const key = paper!;
      const prev = state.papers.get(key);
      state.papers.set(key, {
        key,
        title: prev?.title ?? String(ev['file'] ?? key),
        doi: (ev['doi'] as string | null) ?? prev?.doi ?? null,
        file: String(ev['file'] ?? ''),
        format: String(ev['file'] ?? '').endsWith('.xml') ? 'jats' : 'pdf',
        pages: (ev['pages'] as number | null) ?? null,
        status: String(ev['status']),
        error: null,
        nodes: prev?.nodes ?? 0,
        has_methods: prev?.has_methods ?? null,
        seconds: prev?.seconds ?? null,
        roles: prev?.roles,
      });
      log('stage', ev['existed'] ? 'seen before' : 'filed as new', key);
      renderPapers();
      break;
    }
    case 'stage': {
      const stage = String(ev['stage']);
      if (paper) {
        const p = state.papers.get(paper);
        if (p) {
          p.stage = stage;
          p.message = String(ev['message'] ?? '');
          p.elapsed = Number(ev['elapsed'] ?? 0);
          if (stage === 'failed') {
            p.status = 'failed';
            p.error = p.message;
          } else if (stage !== 'saved') p.status = 'parsing';
        }
        renderPapers();
      }
      setStatus(stage === 'saved' || stage === 'failed' ? 'ok' : 'busy', `${stage}: ${ev['message']}`);
      log(stage === 'failed' ? 'failed' : 'stage', `${stage} — ${ev['message']}`, paper);
      break;
    }
    case 'working': {
      if (paper) {
        const p = state.papers.get(paper);
        if (p) {
          p.elapsed = Number(ev['elapsed'] ?? 0);
          renderPapers();
        }
      }
      break;
    }
    case 'tree': {
      if (!paper) break; // an answer to a read, not a paper landing
      const p = state.papers.get(paper);
      if (p) {
        p.status = 'parsed';
        p.title = String(ev['title'] ?? p.title);
        p.nodes = Number(ev['nodes'] ?? 0);
        p.roles = ev['roles'] as Record<string, number>;
        p.has_methods = ev['has_methods'] ? 1 : 0;
        p.seconds = (ev['seconds'] as number | undefined) ?? null;
        p.pages = Number(ev['pages'] ?? p.pages ?? 0);
      }
      renderPapers();
      log('stage', `tree saved: ${JSON.stringify(ev['roles'])}`, paper);
      if (state.selectedPaper === paper) void loadTree(paper);
      else if (!state.selectedPaper) void selectPaper(paper);
      break;
    }
    case 'done':
      setStatus('ok', 'idle');
      void loadPapers();
      break;
    case 'log':
      log('log', `${ev['logger']}: ${ev['message']}`, paper);
      break;
    case 'skipped':
      log('failed', `skipped ${ev['path']}: ${ev['reason']}`);
      break;
    case 'error':
      log('error', String(ev['message']));
      break;
    case 'worker-error':
      setStatus('bad', String(ev['message']));
      log('error', String(ev['message']));
      break;
    case 'worker-exit':
      setStatus('bad', `worker exited (${ev['code']})`);
      log('error', `worker exited (${ev['code']})\n${ev['tail'] ?? ''}`);
      break;
    case 'stderr':
      // Docling's progress bars and warnings; shown only in the log
      if (!/it\/s|%\|/.test(String(ev['message']))) log('log', String(ev['message']));
      break;
    default:
      break;
  }
}

// ---- wiring ---------------------------------------------------------------------------------

function wire() {
  window.addEventListener('unhandledrejection', (e) => log('error', `unhandled: ${String((e.reason as Error)?.message ?? e.reason)}`));
  window.addEventListener('error', (e) => log('error', `error: ${e.message}`));
  window.litrag.onEvent((ev) => {
    try {
      onEvent(ev);
    } catch (e) {
      log('error', `event ${String(ev['event'])}: ${(e as Error).message}`);
    }
  });
  $<HTMLSelectElement>('library').addEventListener('change', (e) => {
    state.lib = (e.target as HTMLSelectElement).value || null;
    state.selectedPaper = null;
    state.tree = null;
    renderTree();
    void loadPapers();
  });
  $('new-library').addEventListener('click', () => void newLibrary());
  $('add').addEventListener('click', async () => ingest(await window.litrag.choosePdfs()));
  $('reparse').addEventListener('click', async () => {
    if (!state.lib || !state.papers.size) return;
    if (!window.confirm(`Read all ${state.papers.size} papers again with Docling?`)) return;
    await window.litrag.request('reparse', { lib: state.lib });
  });
  $('prev-page').addEventListener('click', () => void showPage(state.page - 1));
  $('next-page').addEventListener('click', () => void showPage(state.page + 1));
  $('clear-log').addEventListener('click', () => ($('log').innerHTML = ''));
  window.addEventListener('resize', () => void (state.pdf && showPage(state.page)));

  const overlay = $('drop-overlay');
  window.addEventListener('dragover', (e) => {
    e.preventDefault();
    overlay.hidden = false;
  });
  window.addEventListener('dragleave', (e) => {
    if (!e.relatedTarget) overlay.hidden = true;
  });
  window.addEventListener('drop', (e) => {
    e.preventDefault();
    overlay.hidden = true;
    const files = [...(e.dataTransfer?.files ?? [])];
    const paths = window.litrag.pathsOf(files).filter((p) => /\.(pdf|xml)$/i.test(p));
    void ingest(paths);
  });

  void window.litrag.info().then((i) => log('log', `worker: ${i.command} · root: ${i.root}`));
  // If the worker was already up before this page loaded, `ready` is gone; ask anyway.
  window.litrag.request('hello').then(() => {
    setStatus('ok', 'worker ready');
    void loadLibraries();
  }).catch(() => setStatus('bad', 'worker did not answer'));
}

wire();
