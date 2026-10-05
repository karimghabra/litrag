/**
 * The Graph tab: the project's papers and the citations between them, drawn as a graph
 * (graphview.ts), the works a citation round found beside them, and SQL over the same rows —
 * `works`, `authors`, `cites` (parser/litrag_parser/graph.py). A query's rows are a way to choose
 * the next papers: the candidates among them can be fetched and read from here, which is the
 * next round of the library.
 */

import { GraphView, type GraphData, type GraphNode } from './graphview.ts';
import { $, activity, ctx, el, hooks, log, onProjectChange, onViewShown, onWorkerEvent, projectName, request } from './shared.ts';

/** A SQL string literal. */
export const sqlString = (s: string): string => `'${s.replace(/'/g, "''")}'`;

export interface Preset {
  label: string;
  title: string;
  sql: (family?: string | null) => string;
}

/** Questions the rows answer, as a person would start them; each is only SQL, there to edit. */
export const PRESETS: Preset[] = [
  {
    label: 'Held, oldest first',
    title: 'The papers this project holds, in the order they were published',
    sql: () => "select year, first_author, title, journal, round, cited_here, work\nfrom works where state = 'held'\norder by year, published",
  },
  {
    label: 'Next round',
    title: 'The works a citation round found and the project does not hold yet, the most cited by its papers first',
    sql: () => "select cited_here, year, first_author, title, status, work\nfrom works\nwhere state = 'candidate' and status in ('found', 'needs-pdf', 'failed')\norder by cited_here desc, year desc",
  },
  {
    label: 'By an author',
    title: 'Every work of one author, held or not, in the order published',
    sql: (family) => `select w.year, a.name, w.title, w.state, w.status, w.work\nfrom authors a join works w using (work)\nwhere a.family = ${sqlString(family || 'Akkus')}\norder by w.year, w.published`,
  },
  {
    label: 'Authors here',
    title: 'The people behind the works, the most papers held first',
    sql: () => "select a.person, min(a.name) as name, sum(w.state = 'held') as held, count(*) as works,\n       min(w.year) as since, max(w.year) as latest, max(a.orcid) as orcid\nfrom authors a join works w using (work)\ngroup by a.person order by held desc, works desc",
  },
  {
    label: 'Who cites whom',
    title: 'Every citation between two works, with both titles',
    sql: () => "select a.year as year, a.first_author as citing, b.year as cited_year, b.first_author as cited, b.title as cited_title, c.origin\nfrom cites c join works a on a.work = c.citing join works b on b.work = c.cited\norder by a.year, b.year",
  },
  {
    label: 'Rounds',
    title: 'How the project grew: each round, held and not',
    sql: () => 'select round, state, count(*) as works from works group by round, state order by round, state',
  },
];

/** The works a query's rows name: a `work` column, else a candidate's id, else a paper's key. */
export function worksOf(columns: string[], rows: Record<string, unknown>[]): (string | null)[] {
  const col = ['work', 'cand_id', 'paper', 'key', 'paper_key'].find((c) => columns.includes(c));
  return rows.map((r) => {
    if (!col) return null;
    const v = r[col];
    if (v === null || v === undefined || v === '') return null;
    return col === 'cand_id' ? `cand:${v}` : String(v);
  });
}

const FETCHABLE = new Set(['found', 'needs-pdf', 'failed', 'staged']);

/** A row's candidate, when it is one that can still be fetched. */
export function fetchableCand(work: string | null, status: unknown): number | null {
  const m = /^cand:(\d+)$/.exec(work ?? '');
  if (!m) return null;
  if (status !== undefined && status !== null && !FETCHABLE.has(String(status))) return null;
  return Number(m[1]);
}

/** An author's Europe PMC query: family name and initials, the way its records print them. */
export function authorQuery(family: string, initials?: string | null): string {
  return `AUTH:"${family}${initials ? ` ${initials}` : ''}"`;
}

interface Author {
  name: string;
  family: string | null;
  initials: string | null;
  orcid: string | null;
  person: string | null;
}

const state = {
  view: null as GraphView | null,
  data: null as GraphData | null,
  selected: null as GraphNode | null,
  picked: new Set<number>(),
  loading: false,
  /** asked for again while loading: one more load once this one is done */
  again: false,
  /** watches the end of the SQL table for the next rows */
  more: null as IntersectionObserver | null,
};

/** Rows of a query's table put down at a time: 2,000 laid out at once held the window for a second. */
const ROWS_AT_ONCE = 200;

export function initGraph(): void {
  const presets = $('sql-presets');
  for (const p of PRESETS) {
    const chip = el('span', 'chip', p.label);
    chip.title = p.title;
    chip.addEventListener('click', () => {
      $<HTMLTextAreaElement>('sql-q').value = p.sql(state.selected?.first_author?.split(' ')[0] ?? null);
      void runSql();
    });
    presets.append(chip);
  }
  $<HTMLTextAreaElement>('sql-q').value = PRESETS[0]!.sql();
  $<HTMLFormElement>('sql-form').addEventListener('submit', (e) => {
    e.preventDefault();
    void runSql();
  });
  $<HTMLTextAreaElement>('sql-q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void runSql();
    }
  });
  $('sql-fetch').addEventListener('click', () => void fetchPicked());
  $('round-run').addEventListener('click', () => void runRound(null));
  $('expand-run').addEventListener('click', () => void runExpand());
  for (const id of ['graph-cands', 'graph-min']) $(id).addEventListener('change', () => void loadGraph());
  $<HTMLSelectElement>('graph-colour').addEventListener('change', () => state.view?.setColourBy($<HTMLSelectElement>('graph-colour').value as 'round' | 'year' | 'state'));
  $<HTMLInputElement>('graph-find').addEventListener('input', () => {
    const q = $<HTMLInputElement>('graph-find').value.trim();
    if (!state.view) return;
    if (!q) state.view.highlight(null);
    else state.view.find(q);
  });
  $('graph-fit').addEventListener('click', () => state.view?.fit());
  onProjectChange(() => {
    state.data = null;
    state.selected = null;
    state.picked = new Set();
    state.more?.disconnect();
    state.more = null;
    $('sql-results').innerHTML = '';
    $('sql-meta').textContent = '';
    void renderDetail();
    if (ctx.view === 'graph') void loadGraph();
  });
  onViewShown((v) => {
    if (v === 'graph') void loadGraph();
  });
  onWorkerEvent((ev) => {
    if (ev['event'] !== 'done' || ev['lib'] !== ctx.lib) return;
    if (ev['op'] === 'round') {
      activity.hide();
      const e = ev as Record<string, unknown>;
      const o = (e['openalex'] ?? {}) as { on?: boolean; works?: number; matched?: number; searches?: number; spent?: boolean };
      const errors = (e['errors'] as string[] | undefined) ?? [];
      log('stage', `Citation round: ${e['papers']} papers asked, ${e['added']} new candidates, ${e['held']} citations between papers held, ${e['unidentified']} entries naming no identifier${o.on ? `; OpenAlex: ${o.works ?? 0} works in its lists, ${o.matched ?? 0} of ${o.searches ?? 0} entries matched by title${o.spent ? ', its daily budget spent' : ''}` : ''}${errors.length ? ` — ${errors.join('; ')}` : ''}`);
      if (ctx.view === 'graph') {
        void loadGraph();
        $<HTMLTextAreaElement>('sql-q').value = PRESETS[1]!.sql();
        void runSql();
      }
    } else if (ev['op'] === 'expand') {
      activity.hide();
      const e = ev as { chosen?: unknown[]; read?: string[]; passages?: number; for_a_person?: number };
      const chosen = e.chosen ?? [];
      const person = e.for_a_person ?? 0;
      log('stage', chosen.length || person
        ? `Expanded: ${e.read?.length ?? 0} papers read of ${chosen.length} that looked readable; ${e.passages ?? 0} passages of the papers held now lead to them${person ? `. ${person} more cited ones have nothing open on record: Collect PDFs walks them` : ''}`
        : 'Expanded: nothing left to read — no candidate the papers cite that has not been fetched or set aside');
      if (ctx.view === 'graph') void loadGraph();
    } else if ((ev['op'] === 'fetch' || ev['op'] === 'ingest') && ctx.view === 'graph') {
      void loadGraph();
    }
  });
}

async function loadGraph(): Promise<void> {
  $('graph-project').textContent = projectName();
  const lib = ctx.lib;
  if (!lib) return;
  if (state.loading) {
    state.again = true;
    return;
  }
  state.loading = true;
  try {
    const candidates = $<HTMLSelectElement>('graph-cands').value;
    const min = Math.max(1, Number($<HTMLInputElement>('graph-min').value) || 2);
    $<HTMLInputElement>('graph-min').disabled = candidates !== 'cited';
    const r = await request<GraphData & { hidden: number }>('graph', { lib, candidates, min_cited: min });
    if (ctx.lib !== lib) return;
    state.data = { nodes: r.nodes, edges: r.edges };
    const held = r.nodes.filter((n) => n.state === 'held').length;
    $('graph-summary').textContent = `${held} held · ${r.nodes.length - held} candidates shown${r.hidden ? ` · ${r.hidden} hidden` : ''} · ${r.edges.length} citations`;
    const host = $('graph-host');
    if (!state.view) {
      state.view = new GraphView(host, {
        onSelect: (n) => {
          state.selected = n;
          void renderDetail();
        },
        onOpen: (n) => {
          if (n.paper) hooks.openPaper(n.paper);
        },
        colourBy: $<HTMLSelectElement>('graph-colour').value as 'round' | 'year' | 'state',
      });
    }
    state.view.setData(state.data);
    $('graph-empty').hidden = r.nodes.length > 0;
    if (state.selected) state.selected = r.nodes.find((n) => n.id === state.selected!.id) ?? null;
    void renderDetail();
  } catch (e) {
    log('error', `graph: ${(e as Error).message}`);
  } finally {
    state.loading = false;
    if (state.again) {
      state.again = false;
      void loadGraph();
    }
  }
}

async function runRound(papers: string[] | null): Promise<void> {
  if (!ctx.lib) return;
  const citations = $<HTMLInputElement>('round-citing').checked;
  const openalex = $<HTMLInputElement>('round-openalex').checked;
  try {
    activity.show(papers ? 'Citation round from one paper' : 'Citation round');
    await request('round', { lib: ctx.lib, ...(papers ? { papers } : {}), citations, openalex });
    log('stage', papers ? `Asking what ${papers[0]} cites${citations ? ', and what cites it' : ''}` : `Asking what the project's papers cite${citations ? ', and what cites them' : ''}: each work found is filed as a candidate of the next round, nothing fetched`);
  } catch (e) {
    activity.hide();
    log('error', `round: ${(e as Error).message}`);
  }
}

/** The library grown by what its papers cite: a round, then the most cited works fetched and read. */
async function runExpand(): Promise<void> {
  if (!ctx.lib) return;
  const most = Math.max(1, Math.min(200, Number($<HTMLInputElement>('expand-most').value) || 10));
  try {
    activity.show(`Expanding: the ${most} works the papers cite most`);
    await request('expand', { lib: ctx.lib, most, citations: $<HTMLInputElement>('round-citing').checked, openalex: $<HTMLInputElement>('round-openalex').checked });
    log('stage', `Expanding: a citation round, then the ${most} works the papers cite most fetched and read (each takes a minute or so to read)`);
  } catch (e) {
    activity.hide();
    log('error', `expand: ${(e as Error).message}`);
  }
}

async function fetchIds(ids: number[]): Promise<void> {
  if (!ctx.lib || !ids.length) return;
  try {
    activity.show(`Fetching ${ids.length} paper${ids.length === 1 ? '' : 's'}`, 0, ids.length);
    await request('fetch', { lib: ctx.lib, ids });
    log('stage', `Fetching ${ids.length}: the XML where it is open, else an open PDF, else marked as needing one`);
  } catch (e) {
    activity.hide();
    log('error', `fetch: ${(e as Error).message}`);
  }
}

async function fetchPicked(): Promise<void> {
  const ids = [...state.picked];
  state.picked = new Set();
  updateFetchButton();
  for (const b of document.querySelectorAll<HTMLInputElement>('#sql-results input[type=checkbox]')) b.checked = false;
  await fetchIds(ids);
}

function updateFetchButton(): void {
  const b = $<HTMLButtonElement>('sql-fetch');
  b.disabled = state.picked.size === 0;
  b.textContent = state.picked.size ? `Fetch & read ${state.picked.size}` : 'Fetch & read selected';
}

async function runSql(): Promise<void> {
  const lib = ctx.lib;
  const sql = $<HTMLTextAreaElement>('sql-q').value.trim();
  if (!lib || !sql) return;
  const box = $('sql-results');
  const meta = $('sql-meta');
  const t = performance.now();
  try {
    const r = await request<{ columns: string[]; rows: Record<string, unknown>[] }>('sql', { lib, sql, limit: 2000 });
    if (ctx.lib !== lib) return;
    renderRows(r.columns, r.rows);
    meta.textContent = `${r.rows.length.toLocaleString()} row${r.rows.length === 1 ? '' : 's'}${r.rows.length >= 2000 ? ' (the first 2000)' : ''} · ${Math.round(performance.now() - t)} ms`;
  } catch (e) {
    box.innerHTML = '';
    meta.textContent = '';
    box.append(el('div', 'empty', (e as Error).message));
  }
}

function renderRows(columns: string[], rows: Record<string, unknown>[]): void {
  const box = $('sql-results');
  state.more?.disconnect();
  state.more = null;
  box.innerHTML = '';
  state.picked = new Set();
  updateFetchButton();
  if (!rows.length) {
    box.append(el('div', 'empty', 'No rows.'));
    state.view?.highlight(null);
    return;
  }
  const works = worksOf(columns, rows);
  const named = new Set(works.filter((w): w is string => !!w));
  state.view?.highlight(named.size ? named : null);
  const statuses = columns.includes('status');
  const rowAt = (i: number): HTMLElement => {
    const row = rows[i]!;
    const tr = el('tr');
    const cell = el('td', 'pick');
    const cand = fetchableCand(works[i] ?? null, statuses ? row['status'] : undefined);
    if (cand !== null) {
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.title = 'Fetch and read this one';
      box.addEventListener('click', (e) => e.stopPropagation());
      box.addEventListener('change', () => {
        if (box.checked) state.picked.add(cand);
        else state.picked.delete(cand);
        updateFetchButton();
      });
      cell.append(box);
    }
    tr.append(cell);
    for (const c of columns) {
      const v = row[c];
      tr.append(el('td', typeof v === 'number' ? 'num' : undefined, v === null || v === undefined ? '' : String(v)));
    }
    const work = works[i];
    if (work) {
      tr.classList.add('linked');
      tr.title = 'Show it in the graph';
      tr.addEventListener('click', () => {
        const n = state.data?.nodes.find((x) => x.id === work);
        if (n) {
          state.view?.focus(work);
          state.selected = n;
          void renderDetail();
        }
      });
    }
    return tr;
  };
  const table = el('table', 'sql-table');
  const head = el('tr');
  head.append(el('th'));
  for (const c of columns) head.append(el('th', undefined, c));
  table.append(head);
  let shown = 0;
  const more = (): void => {
    const end = typeof IntersectionObserver === 'undefined' ? rows.length : Math.min(rows.length, shown + ROWS_AT_ONCE);
    const part = document.createDocumentFragment();
    for (; shown < end; shown++) part.append(rowAt(shown));
    table.append(part);
  };
  more();
  box.append(table);
  if (shown >= rows.length) return;
  // the rest as the table is scrolled towards them
  const end = el('div', 'muted more-rows', `${(rows.length - shown).toLocaleString()} more rows below`);
  box.append(end);
  state.more = new IntersectionObserver((seen) => {
    if (!seen.some((e) => e.isIntersecting)) return;
    more();
    if (shown < rows.length) end.textContent = `${(rows.length - shown).toLocaleString()} more rows below`;
    else {
      state.more?.disconnect();
      state.more = null;
      end.remove();
    }
  }, { root: box, rootMargin: '600px 0px' });
  state.more.observe(end);
}

/** The chosen work: what it is, who wrote it, what it cites and what cites it, and what can be done with it. */
async function renderDetail(): Promise<void> {
  const box = $('graph-detail');
  box.innerHTML = '';
  const n = state.selected;
  $('graph-detail-title').textContent = n ? (n.state === 'held' ? 'A paper held' : 'A candidate') : 'A work';
  if (!n) {
    box.append(el('div', 'empty', 'Click a paper in the graph, or a row below. Double-click a held paper to open it.'));
    return;
  }
  box.append(el('div', 'detail-title', n.title ?? n.label));
  const facts = [n.year ? String(n.year) : null, n.journal, n.state === 'held' ? `held · ${n.status}` : n.status, `round ${n.round}`, `cited here ${n.cited_here}`, `cites here ${n.cites_here}`].filter(Boolean);
  box.append(el('div', 'muted', facts.join(' · ')));
  const actions = el('div', 'actions');
  if (n.paper) {
    const open = el('button', 'ghost small', 'Open in Papers');
    open.addEventListener('click', () => hooks.openPaper(n.paper!));
    const round = el('button', 'ghost small', 'Citation round from it');
    round.title = 'What this paper cites (and, with the box above ticked, what cites it), filed as candidates';
    round.addEventListener('click', () => void runRound([n.paper!]));
    actions.append(open, round);
  }
  const cand = fetchableCand(n.id, n.status);
  if (cand !== null) {
    const fetch = el('button', 'primary small', 'Fetch & read');
    fetch.addEventListener('click', () => void fetchIds([cand]));
    actions.append(fetch);
  }
  box.append(actions);
  // its authors, each a way to the library's other works of theirs and to Europe PMC's
  const lib = ctx.lib;
  const authors = el('div', 'authors');
  box.append(el('h3', undefined, 'Authors'), authors);
  try {
    const r = await request<{ rows: Author[] }>('sql', { lib, sql: `select name, family, initials, orcid, person from authors where work = ${sqlString(n.id)} order by pos`, limit: 200 });
    if (ctx.lib !== lib || state.selected?.id !== n.id) return;
    if (!r.rows.length) authors.append(el('div', 'muted', 'No authors on record.'));
    for (const a of r.rows) {
      const row = el('div', 'author');
      row.append(el('span', 'name', a.name));
      if (a.orcid) {
        const o = el('span', 'orcid', 'iD');
        o.title = `ORCID ${a.orcid}`;
        row.append(o);
      }
      if (a.family) {
        const here = el('button', 'ghost small', 'here');
        here.title = `Every work of ${a.name} this project knows, oldest first`;
        here.addEventListener('click', () => {
          $<HTMLTextAreaElement>('sql-q').value = `select w.year, a.name, w.title, w.state, w.status, w.work\nfrom authors a join works w using (work)\nwhere a.person = ${sqlString(a.person ?? '')}\norder by w.year, w.published`;
          void runSql();
        });
        const out = el('button', 'ghost small', 'Europe PMC');
        out.title = `Search Europe PMC for ${a.name}'s papers: a new round of candidates`;
        out.addEventListener('click', () => hooks.search(authorQuery(a.family!, a.initials)));
        row.append(here, out);
      }
      authors.append(row);
    }
  } catch (e) {
    authors.append(el('div', 'muted', (e as Error).message));
  }
  // the passages of the papers held whose citations lead to it
  await renderPassages(box, n);
  // its neighbours in the graph
  const data = state.data;
  if (!data) return;
  const byId = new Map(data.nodes.map((x) => [x.id, x]));
  for (const [title, ids] of [
    ['Cites', data.edges.filter((e) => e.src === n.id).map((e) => e.dst)],
    ['Cited by', data.edges.filter((e) => e.dst === n.id).map((e) => e.src)],
  ] as const) {
    if (!ids.length) continue;
    box.append(el('h3', undefined, `${title} (${ids.length})`));
    const list = el('div', 'neighbours');
    for (const id of ids) {
      const m = byId.get(id);
      if (!m) continue;
      const item = el('div', `neighbour ${m.state}`, `${m.label} — ${m.title ?? ''}`);
      item.addEventListener('click', () => {
        state.view?.focus(m.id);
        state.selected = m;
        void renderDetail();
      });
      list.append(item);
    }
    box.append(list);
  }
}

interface Passage {
  paper: string;
  paper_title: string | null;
  paper_year: string | null;
  node_id: string;
  ref_no: number;
  marker: string;
  how: string;
  role: string;
  ancestry: string[];
  text: string;
}

/** Where the library's own text cites a work: each passage, its paper and place, a click away. */
async function renderPassages(box: HTMLElement, n: GraphNode): Promise<void> {
  const lib = ctx.lib;
  try {
    const r = await request<{ passages: Passage[] }>('passages', { lib, work: n.id });
    if (ctx.lib !== lib || state.selected?.id !== n.id || !r.passages.length) return;
    const head = el('h3', undefined, `Cited in the text (${r.passages.length})`);
    head.title = 'The passages of the papers held whose citations name this work, and how each entry was linked to it';
    box.append(head);
    const list = el('div', 'neighbours');
    for (const p of r.passages) {
      const item = el('div', 'passage');
      const where = [p.paper_title ?? p.paper, p.paper_year].filter(Boolean).join(', ');
      item.append(el('div', 'where', `${p.marker} · ${where}${p.ancestry.length ? ` · ${p.ancestry.join(' › ')}` : ''}`));
      item.append(el('div', 'quote', quoteAround(p.text, p.marker)));
      item.title = `Open it in the paper (entry [${p.ref_no}], linked by ${p.how})`;
      item.addEventListener('click', () => hooks.openPaper(p.paper, p.node_id));
      list.append(item);
    }
    box.append(list);
  } catch (e) {
    log('error', `passages: ${(e as Error).message}`);
  }
}

/** The words around a marker in a passage: the sentence that cites, not the whole paragraph. */
export function quoteAround(text: string, marker: string, width = 220): string {
  const at = marker ? text.indexOf(marker) : -1;
  if (at < 0 || text.length <= width) return text.length > width ? `${text.slice(0, width)}…` : text;
  const start = Math.max(0, at - Math.floor(width * 0.6));
  const end = Math.min(text.length, start + width);
  return `${start > 0 ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}`;
}
