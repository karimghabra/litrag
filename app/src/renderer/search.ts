/**
 * The Search tab: a Europe PMC query, its hits with what can be had of each (open XML, an open
 * PDF, or nothing open), and the project's candidates — every hit any search found, with where
 * it stands: found, fetched, read, or waiting for a PDF someone downloads by hand. Fetching
 * takes the XML first, an open PDF second, and hands both to the reader; a paper with neither
 * is listed with its links, and a PDF dropped on the window is filed against it.
 */

import { $, activity, ctx, el, log, onProjectChange, onViewShown, onWorkerEvent, projectName, queryText, request } from './shared.ts';

export interface Candidate {
  cand_id: number;
  query?: string | null;
  pmid?: string | null;
  pmcid?: string | null;
  doi?: string | null;
  title?: string | null;
  authors?: string | null;
  journal?: string | null;
  year?: string | number | null;
  abstract?: string | null;
  is_open_access?: number | boolean | null;
  has_xml?: number | boolean | null;
  has_pdf?: number | boolean | null;
  status: string;
  paper_key?: string | null;
  error?: string | null;
  links?: Record<string, string>;
}

const state = {
  query: '',
  hits: [] as Candidate[],
  total: 0,
  next: null as string | null,
  candidates: [] as Candidate[],
  filter: null as string | null,
  selected: new Set<number>(),
};

export function initSearch(): void {
  $<HTMLFormElement>('search-form').addEventListener('submit', (e) => {
    e.preventDefault();
    void runSearch($<HTMLInputElement>('search-q').value.trim(), false);
  });
  $('search-more').addEventListener('click', () => void runSearch(state.query, true));
  $('search-all').addEventListener('change', (e) => {
    const on = (e.target as HTMLInputElement).checked;
    state.selected = on ? new Set(state.hits.filter(fetchable).map((h) => h.cand_id)) : new Set();
    renderHits();
  });
  $('search-fetch').addEventListener('click', () => void fetchSelected());
  $('search-suggest').addEventListener('click', () => void suggest());
  onProjectChange(() => {
    state.hits = [];
    state.total = 0;
    state.next = null;
    state.selected = new Set();
    $('search-suggestions').innerHTML = '';
    delete $('search-suggestions').dataset['kind'];
    if (ctx.view === 'search') {
      $('search-project').textContent = projectName();
      renderPast();
      void loadCandidates();
    }
    renderHits();
  });
  onViewShown((v) => {
    if (v === 'search') {
      $('search-project').textContent = projectName();
      renderPast();
      void loadCandidates();
    }
  });
  onWorkerEvent((ev) => {
    const kind = ev['event'];
    if (kind === 'candidate') {
      const id = Number(ev['cand_id']);
      for (const list of [state.hits, state.candidates]) {
        const c = list.find((x) => x.cand_id === id);
        if (c) {
          c.status = String(ev['status']);
          c.error = (ev['error'] as string | null) ?? null;
          if (ev['paper_key']) c.paper_key = String(ev['paper_key']);
        }
      }
      renderHits();
      renderCandidates();
    } else if (kind === 'done' && (ev['op'] === 'fetch' || ev['op'] === 'ingest')) {
      if (ctx.view === 'search') void loadCandidates();
    }
  });
}

const fetchable = (c: Candidate) => ['found', 'failed', 'dismissed', 'staged'].includes(c.status);

/** The project's searches so far, as chips: a click runs one again (nothing is added twice). */
function renderPast(): void {
  const box = $('search-suggestions');
  if (box.dataset['kind'] === 'suggested') return;
  box.innerHTML = '';
  const past = [...new Set((ctx.projects.find((p) => p.id === ctx.lib)?.queries ?? []).map(queryText))];
  if (!past.length) return;
  box.dataset['kind'] = 'past';
  box.append(el('span', 'muted', 'Searches so far:'));
  for (const q of past.slice(-12)) {
    const chip = el('span', 'chip past', q);
    chip.title = 'Run this search again';
    chip.addEventListener('click', () => {
      $<HTMLInputElement>('search-q').value = q;
      void runSearch(q, false);
    });
    box.append(chip);
  }
}

async function runSearch(query: string, more: boolean): Promise<void> {
  if (!ctx.lib) {
    window.alert('Choose or make a project first.');
    return;
  }
  if (!query) return;
  $('search-total').textContent = 'searching…';
  try {
    const r = await request<{ hits: Candidate[]; total: number; next_cursor: string | null; added: number }>('search', { lib: ctx.lib, query, cursor: more ? state.next : '*' });
    state.query = query;
    state.hits = more ? [...state.hits, ...r.hits] : r.hits;
    state.total = r.total;
    state.next = r.next_cursor && r.hits.length ? r.next_cursor : null;
    if (!more) state.selected = new Set();
    log('stage', `Europe PMC: ${r.total.toLocaleString()} for ${query} · ${r.added} new candidates`);
    renderHits();
    void loadCandidates();
  } catch (e) {
    $('search-total').textContent = '';
    log('error', `search: ${(e as Error).message}`);
    window.alert(`The search failed: ${(e as Error).message}`);
  }
}

function availability(c: Candidate): HTMLElement[] {
  const out: HTMLElement[] = [];
  if (c.has_xml) out.push(el('span', 'pill xml', 'open XML'));
  if (c.has_pdf) out.push(el('span', 'pill pdf', 'open PDF'));
  if (!c.has_xml && !c.has_pdf) out.push(el('span', 'pill closed', 'no open full text'));
  return out;
}

function statusPill(c: Candidate): HTMLElement {
  const label = { found: 'found', staged: 'staged', fetching: 'fetching…', fetched: 'fetched', 'needs-pdf': 'needs PDF', ingested: 'in library', failed: 'failed', dismissed: 'dismissed' }[c.status] ?? c.status;
  const p = el('span', `pill st-${c.status}`, label);
  if (c.error) p.title = c.error;
  return p;
}

function meta(c: Candidate): string {
  return [c.authors ? String(c.authors).split(',').slice(0, 3).join(',') + (String(c.authors).split(',').length > 3 ? ' et al.' : '') : '', c.journal ?? '', c.year ?? '', c.doi ? `doi:${c.doi}` : c.pmcid ?? (c.pmid ? `pmid:${c.pmid}` : '')].filter(Boolean).join(' · ');
}

function linkRow(c: Candidate): HTMLElement | null {
  const links: [string, string][] = [];
  if (c.doi) links.push(['publisher', `https://doi.org/${c.doi}`]);
  if (c.pmid) links.push(['Europe PMC', `https://europepmc.org/article/MED/${c.pmid}`]);
  else if (c.pmcid) links.push(['Europe PMC', `https://europepmc.org/article/PMC/${c.pmcid}`]);
  if (!links.length) return null;
  const row = el('span', 'row');
  for (const [label, href] of links) {
    const a = el('a', undefined, label) as HTMLAnchorElement;
    a.href = '#';
    a.addEventListener('click', (e) => {
      e.preventDefault();
      void window.litrag.openExternal(href);
    });
    row.append(a);
  }
  return row;
}

function renderHits(): void {
  const box = $('search-results');
  box.innerHTML = '';
  $('search-total').textContent = state.total ? `${state.hits.length} of ${state.total.toLocaleString()}` : '';
  $('search-more').hidden = !state.next;
  if (!state.hits.length) {
    box.append(el('div', 'empty', ctx.lib ? 'Search Europe PMC: its query syntax works (AND, OR, quotes, TITLE:, ABSTRACT:). Every hit is kept as a candidate of this project.' : 'Choose a project first.'));
  }
  for (const c of state.hits) {
    const row = el('div', 'hit');
    row.dataset['cand'] = String(c.cand_id);
    const cb = el('input') as HTMLInputElement;
    cb.type = 'checkbox';
    cb.checked = state.selected.has(c.cand_id);
    cb.disabled = !fetchable(c);
    cb.addEventListener('change', () => {
      if (cb.checked) state.selected.add(c.cand_id);
      else state.selected.delete(c.cand_id);
      updateFetchButton();
    });
    row.append(cb, el('div', 't', c.title ?? '(untitled)'));
    row.append(el('div', 'm', meta(c)));
    if (c.abstract) {
      const ab = el('div', 'ab', c.abstract);
      ab.title = c.abstract;
      row.append(ab);
    }
    const flags = el('div', 'flags');
    flags.append(statusPill(c), ...availability(c));
    const links = linkRow(c);
    if (links) flags.append(links);
    row.append(flags);
    box.append(row);
  }
  updateFetchButton();
}

function updateFetchButton(): void {
  const n = state.selected.size;
  const b = $<HTMLButtonElement>('search-fetch');
  b.disabled = n === 0;
  b.textContent = n ? `Fetch & read ${n}` : 'Fetch & read selected';
}

async function fetchSelected(): Promise<void> {
  if (!ctx.lib || !state.selected.size) return;
  const ids = [...state.selected];
  try {
    activity.show(`Fetching ${ids.length} paper${ids.length === 1 ? '' : 's'}`, 0, ids.length);
    await request('fetch', { lib: ctx.lib, ids });
    state.selected = new Set();
    for (const c of state.hits) if (ids.includes(c.cand_id)) c.status = 'staged';
    renderHits();
    log('stage', `Fetching ${ids.length}: the XML where it is open, else an open PDF, else marked as needing one`);
  } catch (e) {
    activity.hide();
    log('error', `fetch: ${(e as Error).message}`);
  }
}

export async function loadCandidates(): Promise<void> {
  if (!ctx.lib) {
    state.candidates = [];
    renderCandidates();
    return;
  }
  try {
    const r = await request<{ candidates: Candidate[] }>('candidates', { lib: ctx.lib });
    state.candidates = r.candidates;
  } catch (e) {
    log('error', `candidates: ${(e as Error).message}`);
    state.candidates = [];
  }
  renderCandidates();
}

function renderCandidates(): void {
  const chips = $('cand-filter');
  chips.innerHTML = '';
  const counts = new Map<string, number>();
  for (const c of state.candidates) counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
  for (const [status, n] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
    const chip = el('span', `chip accent${state.filter === status ? ' on' : ''}`, `${status} ${n}`);
    chip.dataset['status'] = status;
    chip.addEventListener('click', () => {
      state.filter = state.filter === status ? null : status;
      renderCandidates();
    });
    chips.append(chip);
  }
  const box = $('candidates');
  box.innerHTML = '';
  const shown = state.candidates.filter((c) => !state.filter || c.status === state.filter);
  if (!shown.length) box.append(el('div', 'empty', 'No candidates yet: every hit of a search lands here.'));
  for (const c of shown) {
    const row = el('div', 'hit');
    row.dataset['cand'] = String(c.cand_id);
    row.append(el('span'), el('div', 't', c.title ?? '(untitled)'));
    row.append(el('div', 'm', meta(c)));
    const flags = el('div', 'flags');
    flags.append(statusPill(c), ...availability(c));
    if (c.status === 'needs-pdf' || c.status === 'failed') {
      const links = linkRow(c);
      if (links) flags.append(links);
    }
    if (fetchable(c)) {
      const b = el('button', 'ghost small', 'Fetch');
      b.addEventListener('click', async () => {
        activity.show('Fetching 1 paper', 0, 1);
        await request('fetch', { lib: ctx.lib, ids: [c.cand_id] }).catch((e: Error) => log('error', e.message));
      });
      flags.append(b);
    }
    row.append(flags);
    box.append(row);
  }
}

async function suggest(): Promise<void> {
  if (!ctx.lib) return;
  const box = $('search-suggestions');
  box.innerHTML = '';
  box.dataset['kind'] = 'suggested';
  box.append(el('span', 'muted', 'Asking the local model for queries from the project’s description…'));
  try {
    const r = await request<{ queries: string[]; model?: string; error?: string }>('suggest', { lib: ctx.lib });
    box.innerHTML = '';
    if (r.error) box.append(el('span', 'muted', r.error));
    for (const q of r.queries ?? []) {
      const chip = el('span', 'chip', q);
      chip.title = `Search for this${r.model ? ` (suggested by ${r.model})` : ''}`;
      chip.addEventListener('click', () => {
        $<HTMLInputElement>('search-q').value = q;
        void runSearch(q, false);
      });
      box.append(chip);
    }
  } catch (e) {
    box.innerHTML = '';
    box.append(el('span', 'muted', `No suggestions: ${(e as Error).message}`));
  }
}
