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
  /** the candidates panel: this search's hits only (once a search has run), or every search's */
  scope: 'search' as 'search' | 'all',
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
  $('collect').addEventListener('click', () => void collect());
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
    if (kind === 'collect') {
      onCollect(ev);
      return;
    }
    if (kind === 'candidate') {
      if (ev['lib'] !== ctx.lib) return; // a candidate number is a row of one project's store, not of this one
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
    } else if ((kind === 'done' && (ev['op'] === 'fetch' || ev['op'] === 'ingest')) || kind === 'paper') {
      // a paper filed is a candidate in the library: the list says so as it happens, not when the batch ends
      if (ctx.view === 'search') soon();
      // a fetch that left papers with no open copy goes straight on to the collect window for them
      if (kind === 'done' && ev['op'] === 'fetch' && ev['lib'] === ctx.lib && !collecting) {
        const wanting = ((ev['fetched'] as { status?: string }[] | undefined) ?? []).filter((f) => f.status === 'needs-pdf').length;
        if (wanting) {
          log('stage', `${wanting} paper${wanting === 1 ? '' : 's'} with no open copy: opening the collect window`);
          void loadCandidates().then(() => collect());
        }
      }
    }
  });
}

let pending: ReturnType<typeof setTimeout> | null = null;
function soon(): void {
  if (pending) return;
  pending = setTimeout(() => {
    pending = null;
    void loadCandidates().then(() => {
      // the hits of the search on screen are the same candidates: their pills follow the list's
      const by = new Map(state.candidates.map((c) => [c.cand_id, c]));
      for (const h of state.hits) {
        const c = by.get(h.cand_id);
        if (c) Object.assign(h, { status: c.status, paper_key: c.paper_key, error: c.error });
      }
      renderHits();
    });
  }, 400);
}

// `needs-pdf` is fetchable again: a route that came later (NCBI's XML for an author manuscript,
// 2026-09-30) may have what an earlier fetch could not find.
const fetchable = (c: Candidate) => ['found', 'failed', 'dismissed', 'staged', 'needs-pdf'].includes(c.status);

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
  const lib = ctx.lib;
  try {
    const r = await request<{ hits: Candidate[]; total: number; next_cursor: string | null; added: number }>('search', { lib, query, cursor: more ? state.next : '*' });
    if (ctx.lib !== lib) return; // another project was chosen while Europe PMC answered: these candidates are not its
    state.query = query;
    state.hits = more ? [...state.hits, ...r.hits] : r.hits;
    state.total = r.total;
    state.next = r.next_cursor && r.hits.length ? r.next_cursor : null;
    if (!more) {
      state.selected = new Set();
      state.scope = 'search';
      state.filter = null;
    }
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

/** The surnames a query asks for by author — AUTH:"Cauwenberghs G", AUTH:Cauwenberghs — lowercased. */
function queryAuthors(query: string): string[] {
  const out: string[] = [];
  for (const m of query.matchAll(/AUTH(?:OR)?\s*:\s*(?:"([^"]+)"|\(([^)]+)\)|(\S+))/gi)) {
    const name = (m[1] ?? m[2] ?? m[3] ?? '').trim();
    const surname = name.split(/\s+/)[0];
    if (surname) out.push(surname.toLowerCase());
  }
  return out;
}

/** A paper's byline: its first author, every author the search named, and its last author (the PI,
 *  in these fields), the gaps marked — so an author a search asked for is never hidden behind
 *  "et al.". The full list is the element's title. */
function byline(c: Candidate, query: string): HTMLElement {
  const box = el('div', 'm');
  const authors = String(c.authors ?? '').split(',').map((a) => a.trim()).filter(Boolean);
  const wanted = queryAuthors(query);
  // a bare word of the query that is an author's surname counts too: "gert cauwenberghs" names him
  const words = new Set((query.toLowerCase().match(/[\p{L}'-]{3,}/gu) ?? []));
  const named = (a: string) => wanted.some((w) => a.toLowerCase().startsWith(w)) || words.has(a.split(/\s+/)[0]!.toLowerCase());
  const keep = new Set<number>([0, 1, 2, authors.length - 1]);
  authors.forEach((a, i) => named(a) && keep.add(i));
  let last = -1;
  authors.forEach((a, i) => {
    if (!keep.has(i)) return;
    if (last >= 0) box.append(document.createTextNode(i === last + 1 ? ', ' : ', … '));
    box.append(named(a) ? el('b', 'named', a) : document.createTextNode(a));
    last = i;
  });
  const rest = [c.journal ?? '', c.year ?? '', c.doi ? `doi:${c.doi}` : c.pmcid ?? (c.pmid ? `pmid:${c.pmid}` : '')].filter(Boolean).join(' · ');
  if (rest) box.append(document.createTextNode(`${authors.length ? ' · ' : ''}${rest}`));
  if (authors.length) box.title = `${authors.length} authors: ${authors.join(', ')}`;
  return box;
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
    box.append(el('div', 'empty', ctx.lib ? 'Search Europe PMC with its own syntax: AND, OR, NOT, "quoted phrases", and fields — AUTH:"Surname I" for an author (a bare name matches anywhere in the text), TITLE:, ABSTRACT:, JOURNAL:, PUB_YEAR:[2020 TO 2026]. Every hit is kept as a candidate of this project.' : 'Choose a project first.'));
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
    row.append(byline(c, state.query));
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
  const lib = ctx.lib;
  try {
    const r = await request<{ candidates: Candidate[] }>('candidates', { lib });
    if (ctx.lib !== lib) return;
    state.candidates = r.candidates;
  } catch (e) {
    log('error', `candidates: ${(e as Error).message}`);
    state.candidates = [];
  }
  renderCandidates();
}

function renderCandidates(): void {
  const wanting = state.candidates.filter((c) => c.status === 'needs-pdf').length;
  const button = $<HTMLButtonElement>('collect');
  button.disabled = !wanting || collecting;
  button.textContent = collecting ? 'Collecting…' : wanting ? `Collect PDFs (${wanting})` : 'Collect PDFs';
  const chips = $('cand-filter');
  chips.innerHTML = '';
  // a project's candidates are every hit of every search it has run; after a search the panel
  // shows that search's, so an earlier, broader query's papers do not read as this one's
  const ids = new Set(state.hits.map((h) => h.cand_id));
  const scoped = state.scope === 'search' && ids.size > 0;
  const pool = scoped ? state.candidates.filter((c) => ids.has(c.cand_id)) : state.candidates;
  if (ids.size) {
    for (const [scope, label, n] of [['search', 'this search', state.candidates.filter((c) => ids.has(c.cand_id)).length], ['all', 'every search', state.candidates.length]] as const) {
      const chip = el('span', `chip scope${state.scope === scope ? ' on' : ''}`, `${label} ${n}`);
      chip.dataset['scope'] = scope;
      chip.title = scope === 'search' ? 'Only the papers the search on the left found' : 'Every paper any search of this project has found';
      chip.addEventListener('click', () => {
        state.scope = scope;
        renderCandidates();
      });
      chips.append(chip);
    }
    chips.append(el('span', 'sep'));
  }
  const counts = new Map<string, number>();
  for (const c of pool) counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
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
  const shown = pool.filter((c) => !state.filter || c.status === state.filter);
  if (!shown.length) box.append(el('div', 'empty', scoped ? 'None of this search’s papers match that filter.' : 'No candidates yet: every hit of a search lands here.'));
  for (const c of shown) {
    const row = el('div', 'hit');
    row.dataset['cand'] = String(c.cand_id);
    row.append(el('span'), el('div', 't', c.title ?? '(untitled)'));
    row.append(byline(c, state.query));
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

// ---- the collect window ----------------------------------------------------------------------

let collecting = false;

/** One browser window through every candidate that needs a PDF (app/src/main/collect.ts): the
 *  person clicks each paper's PDF, the download is caught into the inbox under the paper's key,
 *  and each caught file is read here as a dropped one is — with the candidate's identifiers, so it
 *  files under the paper it was caught for. */
async function collect(): Promise<void> {
  const lib = ctx.lib;
  if (!lib || collecting) return;
  const project = ctx.projects.find((p) => p.id === lib) as ({ dir?: string } & (typeof ctx.projects)[number]) | undefined;
  if (!project?.dir) {
    log('error', 'collect: the project has no folder on disk');
    return;
  }
  let wanted: Candidate[];
  try {
    wanted = (await request<{ candidates: Candidate[] }>('wanted', { lib })).candidates;
  } catch (e) {
    log('error', `collect: ${(e as Error).message}`);
    return;
  }
  if (!wanted.length) return;
  collecting = true;
  renderCandidates();
  const box = $('collect-status');
  box.hidden = false;
  box.textContent = `Opening the collect window for ${wanted.length} paper${wanted.length === 1 ? '' : 's'}…`;
  const inboxDir = `${project.dir.replace(/[\\/]+$/, '')}/inbox`;
  const papers = wanted.map((c) => ({ cand_id: c.cand_id, title: c.title ?? null, doi: c.doi ?? null, pmid: c.pmid ?? null, pmcid: c.pmcid ?? null }));
  try {
    const r = await window.litrag.collect({ lib, inboxDir, papers });
    if (r['ok'] === false) log('error', `collect: ${String(r['message'])}`);
  } catch (e) {
    log('error', `collect: ${(e as Error).message}`);
  } finally {
    collecting = false;
    renderCandidates();
  }
}

function onCollect(ev: Record<string, unknown>): void {
  const lib = String(ev['lib'] ?? '');
  const stage = String(ev['stage'] ?? '');
  const box = $('collect-status');
  if (lib === ctx.lib) {
    box.hidden = false;
    box.innerHTML = '';
    const total = Number(ev['total'] ?? 0);
    const at = Math.min(Number(ev['index'] ?? 0) + 1, total);
    const now = el('span', 'now', stage === 'finished' ? 'Collect window closed' : `Paper ${at} of ${total}`);
    box.append(now, el('span', undefined, `${ev['caught'] ?? 0} caught · ${ev['skipped'] ?? 0} skipped${ev['unlinked'] ? ` · ${ev['unlinked']} with no page to open` : ''}`));
    if (ev['title'] && stage !== 'finished') box.append(el('span', 'muted', String(ev['title'])));
    if (stage === 'not-a-paper' || stage === 'download-failed') box.append(el('span', 'conf low', String(ev['reason'] ?? 'the download failed')));
  }
  if (stage === 'caught' && ev['path']) {
    // read at once, as a dropped file is, under the identifiers of the candidate it was caught for
    const path = String(ev['path']);
    log('stage', `caught ${path.split(/[\\/]/).pop()}: reading it`);
    void request('ingest', { lib, paths: [path], known: { [path]: ev['known'] ?? {} } }).catch((e: Error) => log('error', `ingest: ${e.message}`));
  } else if (stage === 'not-a-paper' || stage === 'download-failed') {
    log('failed', `collect: ${ev['title'] ?? ''} — ${ev['reason'] ?? stage}`);
  } else if (stage === 'finished') {
    log('stage', `collect: ${ev['caught']} caught, ${ev['skipped']} skipped of ${ev['papers'] ?? ev['total']}`);
    if (lib === ctx.lib) void loadCandidates();
  }
}

async function suggest(): Promise<void> {
  if (!ctx.lib) return;
  const box = $('search-suggestions');
  box.innerHTML = '';
  box.dataset['kind'] = 'suggested';
  box.append(el('span', 'muted', 'Asking the local model for queries from the project’s description…'));
  const lib = ctx.lib;
  try {
    const r = await request<{ queries: string[]; model?: string; error?: string }>('suggest', { lib });
    if (ctx.lib !== lib) return;
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
