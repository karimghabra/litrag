/**
 * What each person keeps of their own, seen only by them: the papers they opened lately, their
 * favourites, their collections (folders of papers that are theirs, independent of any project),
 * and their notes on a paper or on one passage of it. Every list is the signed-in account's,
 * asked through account.ts; the papers themselves are everyone's.
 *
 * The sidebar's "Yours" group opens the lists in one view (`view-mine`); the paper being read
 * carries a star, an "Add to collection" menu, and the notes panel.
 */

import { me, mine, onAccountChange } from './account.ts';
import { askText } from './dialog.ts';
import { $, ctx, el, log, onViewShown, rejectionText, showView } from './shared.ts';

export interface MinePaper {
  paper: string;
  project: string | null;
  title: string | null;
  year: string | null;
  journal: string | null;
  format: string | null;
  status: string | null;
  /** the projects that hold the paper now */
  projects: string[];
  viewed_at?: string;
  added_at?: string;
}

export interface Note {
  id: number;
  paper: string;
  node_id: string | null;
  quote: string | null;
  text: string;
  created_at: string;
  updated_at: string;
}

export interface Collection {
  id: number;
  name: string;
  papers: number;
}

/** What the paper view gives this module: the paper open, the passage selected, and a way to open either. */
export interface PaperView {
  open(lib: string, key: string, nodeId?: string): Promise<void>;
  current(): { key: string; nodeId: string | null; quote: string | null } | null;
}

type List = { kind: 'recent' } | { kind: 'favourites' } | { kind: 'collection'; id: number };

const state = {
  view: null as PaperView | null,
  list: { kind: 'recent' } as List,
  collections: [] as Collection[],
  paper: null as string | null,
  notes: [] as Note[],
};

/** The project a paper is opened in: the one it was last seen in while that one still holds it, else the first that does; none when no project holds it now. */
export function homeOf(p: Pick<MinePaper, 'project' | 'projects'>): string | null {
  if (p.project && p.projects.includes(p.project)) return p.project;
  if (ctx.lib && p.projects.includes(ctx.lib)) return ctx.lib;
  return p.projects[0] ?? null;
}

/** "2024 · Micromachines · tendon, bone": what a row says under its title. */
export function rowMeta(p: MinePaper, names: (id: string) => string = (id) => id): string {
  return [p.year, p.journal, p.projects.length ? p.projects.map(names).join(', ') : 'in no project now'].filter(Boolean).join(' · ');
}

function projectName(id: string): string {
  return ctx.projects.find((p) => p.id === id)?.name ?? id;
}

// ---- the sidebar --------------------------------------------------------------------------

async function loadCollections(): Promise<void> {
  if (!me()) {
    state.collections = [];
  } else {
    try {
      state.collections = (await mine<{ collections: Collection[] }>('collections')).collections;
    } catch (e) {
      log('error', `collections: ${rejectionText(e)}`);
    }
  }
  renderSidebar();
  renderPaperBar();
}

function renderSidebar(): void {
  const box = $('nav-collections');
  box.innerHTML = '';
  for (const c of state.collections) {
    const b = el('button', 'sub', c.name) as HTMLButtonElement;
    b.dataset['view'] = 'mine';
    b.dataset['collection'] = String(c.id);
    b.title = `${c.papers} paper${c.papers === 1 ? '' : 's'}`;
    b.addEventListener('click', () => show({ kind: 'collection', id: c.id }));
    box.append(b);
  }
  markSidebar();
}

function markSidebar(): void {
  for (const b of document.querySelectorAll<HTMLButtonElement>('#nav-mine button[data-view="mine"]')) {
    const l = state.list;
    const on = ctx.view === 'mine' && ((b.dataset['mine'] && b.dataset['mine'] === l.kind) || (l.kind === 'collection' && b.dataset['collection'] === String(l.id)));
    b.classList.toggle('on', Boolean(on));
  }
}

async function newCollection(): Promise<void> {
  const name = await askText('Name of the new collection', { ok: 'Make it' });
  if (!name?.trim()) return;
  try {
    const c = (await mine<{ collection: Collection }>('collection_create', { name: name.trim() })).collection;
    await loadCollections();
    show({ kind: 'collection', id: c.id });
  } catch (e) {
    window.alert(rejectionText(e));
  }
}

// ---- the lists ----------------------------------------------------------------------------

function show(list: List): void {
  state.list = list;
  showView('mine');
  void renderList();
}

async function renderList(): Promise<void> {
  const l = state.list;
  const box = $('mine-list');
  const coll = l.kind === 'collection' ? state.collections.find((c) => c.id === l.id) : undefined;
  $('mine-title').textContent = l.kind === 'recent' ? 'Recent' : l.kind === 'favourites' ? 'Favourites' : coll?.name ?? 'Collection';
  $('mine-rename').hidden = $('mine-delete').hidden = l.kind !== 'collection';
  markSidebar();
  if (!me()) {
    box.innerHTML = '';
    return;
  }
  let papers: MinePaper[] = [];
  try {
    if (l.kind === 'recent') papers = (await mine<{ papers: MinePaper[] }>('recent', { limit: 50 })).papers;
    else if (l.kind === 'favourites') papers = (await mine<{ papers: MinePaper[] }>('favourites')).papers;
    else papers = (await mine<{ papers: MinePaper[] }>('collection', { collection_id: l.id })).papers;
  } catch (e) {
    box.innerHTML = '';
    box.append(el('div', 'empty', rejectionText(e)));
    return;
  }
  if (state.list !== l) return;
  $('mine-count').textContent = papers.length ? String(papers.length) : '';
  box.innerHTML = '';
  if (!papers.length) {
    const why = l.kind === 'recent' ? 'Papers you open appear here, the latest first.'
      : l.kind === 'favourites' ? 'Star a paper (☆ above its tree) to keep it here.'
        : 'Add a paper to this collection from the menu above its tree.';
    box.append(el('div', 'empty', why));
    return;
  }
  for (const p of papers) box.append(row(p, l));
}

function row(p: MinePaper, l: List): HTMLElement {
  const r = el('div', 'card mine-row');
  r.dataset['key'] = p.paper;
  r.append(el('div', 'title', p.title ?? p.paper));
  r.append(el('div', 'muted', rowMeta(p, projectName)));
  const actions = el('div', 'actions');
  const home = homeOf(p);
  const open = el('button', 'primary small', 'Open') as HTMLButtonElement;
  open.disabled = !home;
  open.title = home ? `Open it in ${projectName(home)}` : 'No project holds this paper now';
  open.addEventListener('click', () => {
    if (home) void state.view?.open(home, p.paper);
  });
  actions.append(open);
  if (l.kind !== 'recent') {
    const drop = el('button', 'ghost small', l.kind === 'favourites' ? 'Unstar' : 'Remove from collection');
    drop.addEventListener('click', async () => {
      try {
        if (l.kind === 'favourites') await mine('favourite', { key: p.paper, on: false });
        else if (l.kind === 'collection') await mine('collection_remove', { collection_id: l.id, keys: [p.paper] });
        await Promise.all([renderList(), loadCollections()]);
      } catch (e) {
        log('error', rejectionText(e));
      }
    });
    actions.append(drop);
  }
  r.append(actions);
  return r;
}

async function renameCollection(): Promise<void> {
  const l = state.list;
  if (l.kind !== 'collection') return;
  const c = state.collections.find((x) => x.id === l.id);
  const name = await askText('Rename the collection', { value: c?.name ?? '', ok: 'Rename' });
  if (!name?.trim()) return;
  try {
    await mine('collection_rename', { collection_id: l.id, name: name.trim() });
    await loadCollections();
    void renderList();
  } catch (e) {
    window.alert(rejectionText(e));
  }
}

async function deleteCollection(): Promise<void> {
  const l = state.list;
  if (l.kind !== 'collection') return;
  const c = state.collections.find((x) => x.id === l.id);
  if (!window.confirm(`Delete the collection “${c?.name ?? ''}”? The papers in it stay in their projects.`)) return;
  try {
    await mine('collection_delete', { collection_id: l.id });
    await loadCollections();
    show({ kind: 'recent' });
  } catch (e) {
    window.alert(rejectionText(e));
  }
}

// ---- the paper being read: its star, its collections, its notes -------------------------

/** The paper view says a paper is open: it is this person's recent paper, and its star, collections and notes are drawn. */
export function paperShown(key: string | null): void {
  state.paper = key;
  if (key && me() && ctx.lib) void mine('viewed', { lib: ctx.lib, key }).catch((e) => log('error', `recent: ${rejectionText(e)}`));
  void loadMarks();
}

/** The paper view selected another passage: the notes panel offers a note on it. */
export function passageShown(): void {
  renderNotes();
}

let marks: { favourite: boolean; collections: number[] } = { favourite: false, collections: [] };

async function loadMarks(): Promise<void> {
  const key = state.paper;
  marks = { favourite: false, collections: [] };
  state.notes = [];
  if (key && me()) {
    try {
      const [m, n] = await Promise.all([
        mine<{ favourite: boolean; collections: number[] }>('paper_marks', { key }),
        mine<{ notes: Note[] }>('notes', { key }),
      ]);
      if (state.paper !== key) return;
      marks = { favourite: m.favourite, collections: m.collections };
      state.notes = n.notes;
    } catch (e) {
      log('error', `notes: ${rejectionText(e)}`);
    }
  }
  renderPaperBar();
  renderNotes();
}

function renderPaperBar(): void {
  const bar = $('paper-mine');
  bar.hidden = !state.paper || !me();
  if (bar.hidden) return;
  const star = $<HTMLButtonElement>('paper-star');
  star.textContent = marks.favourite ? '★' : '☆';
  star.title = marks.favourite ? 'A favourite of yours: click to unstar' : 'Star it: keep it in your Favourites';
  star.classList.toggle('on', marks.favourite);
  const sel = $<HTMLSelectElement>('paper-collection');
  sel.innerHTML = '';
  const first = el('option', undefined, state.collections.length ? 'Add to collection…' : 'No collections yet') as HTMLOptionElement;
  first.value = '';
  sel.append(first);
  for (const c of state.collections) {
    const o = el('option', undefined, `${marks.collections.includes(c.id) ? '✓ ' : ''}${c.name}`) as HTMLOptionElement;
    o.value = String(c.id);
    sel.append(o);
  }
  const make = el('option', undefined, 'New collection…') as HTMLOptionElement;
  make.value = 'new';
  sel.append(make);
  $('paper-notes-count').textContent = state.notes.length ? String(state.notes.length) : '';
}

async function toggleStar(): Promise<void> {
  if (!state.paper) return;
  try {
    const r = await mine<{ on: boolean }>('favourite', { key: state.paper, on: !marks.favourite, lib: ctx.lib });
    marks.favourite = r.on;
    renderPaperBar();
  } catch (e) {
    log('error', rejectionText(e));
  }
}

async function chooseCollection(value: string): Promise<void> {
  const key = state.paper;
  if (!key || !value) return;
  try {
    let id = Number(value);
    if (value === 'new') {
      const name = await askText('Name of the new collection', { ok: 'Make it' });
      if (!name?.trim()) return renderPaperBar();
      id = (await mine<{ collection: Collection }>('collection_create', { name: name.trim() })).collection.id;
    }
    const has = marks.collections.includes(id);
    await mine(has ? 'collection_remove' : 'collection_add', { collection_id: id, keys: [key], lib: ctx.lib });
    marks.collections = has ? marks.collections.filter((c) => c !== id) : [...marks.collections, id];
    await loadCollections();
  } catch (e) {
    window.alert(rejectionText(e));
    renderPaperBar();
  }
}

function renderNotes(): void {
  const panel = $('notes-panel');
  panel.hidden = !state.paper || !me() || $('paper-notes-toggle').getAttribute('aria-pressed') !== 'true';
  if (panel.hidden) return;
  const list = $('notes-list');
  list.innerHTML = '';
  if (!state.notes.length) list.append(el('div', 'muted', 'No notes on this paper yet. Only you see your notes.'));
  for (const n of state.notes) list.append(noteRow(n));
  const cur = state.view?.current();
  const onPassage = $<HTMLButtonElement>('note-passage');
  onPassage.disabled = !cur?.nodeId;
  onPassage.title = cur?.nodeId ? `A note on the selected passage: “${(cur.quote ?? '').slice(0, 80)}”` : 'Select a passage in the tree to write a note on it';
}

function noteRow(n: Note): HTMLElement {
  const r = el('div', 'note');
  if (n.node_id) {
    const q = el('button', 'quote', `“${n.quote ?? 'a passage'}”`) as HTMLButtonElement;
    q.title = 'Show the passage';
    q.addEventListener('click', () => {
      if (ctx.lib && state.paper) void state.view?.open(ctx.lib, state.paper, n.node_id ?? undefined);
    });
    r.append(q);
  }
  r.append(el('div', 'text', n.text));
  const foot = el('div', 'foot muted');
  foot.append(document.createTextNode(n.updated_at.replace('T', ' ').replace('Z', '')));
  const edit = el('button', 'ghost small', 'Edit');
  edit.addEventListener('click', async () => {
    const text = await askText('Edit the note', { value: n.text, long: true, ok: 'Save' });
    if (text === null || !text.trim()) return;
    try {
      await mine('note_save', { key: n.paper, note_id: n.id, text });
      await loadMarks();
    } catch (e) {
      window.alert(rejectionText(e));
    }
  });
  const del = el('button', 'ghost small', 'Delete');
  del.addEventListener('click', async () => {
    if (!window.confirm('Delete this note?')) return;
    try {
      await mine('note_delete', { note_id: n.id });
      await loadMarks();
    } catch (e) {
      window.alert(rejectionText(e));
    }
  });
  foot.append(edit, del);
  r.append(foot);
  return r;
}

async function addNote(onPassage: boolean): Promise<void> {
  const key = state.paper;
  const box = $<HTMLTextAreaElement>('note-text');
  const text = box.value.trim();
  if (!key || !text) return;
  const cur = onPassage ? state.view?.current() : null;
  try {
    await mine('note_save', { key, text, node_id: cur?.nodeId ?? undefined, quote: cur?.quote ?? undefined });
    box.value = '';
    await loadMarks();
  } catch (e) {
    window.alert(rejectionText(e));
  }
}

export function initMine(view: PaperView): void {
  state.view = view;
  for (const b of document.querySelectorAll<HTMLButtonElement>('#nav-mine button[data-mine]')) {
    b.addEventListener('click', () => show({ kind: b.dataset['mine'] as 'recent' | 'favourites' }));
  }
  $('new-collection').addEventListener('click', () => void newCollection());
  $('mine-rename').addEventListener('click', () => void renameCollection());
  $('mine-delete').addEventListener('click', () => void deleteCollection());
  $('paper-star').addEventListener('click', () => void toggleStar());
  const sel = $<HTMLSelectElement>('paper-collection');
  sel.addEventListener('change', () => {
    const v = sel.value;
    sel.value = '';
    void chooseCollection(v);
  });
  const toggle = $('paper-notes-toggle');
  toggle.addEventListener('click', () => {
    toggle.setAttribute('aria-pressed', toggle.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    renderNotes();
  });
  $('note-paper').addEventListener('click', () => void addNote(false));
  $('note-passage').addEventListener('click', () => void addNote(true));
  onViewShown((v) => {
    markSidebar();
    if (v === 'mine') void renderList();
  });
  onAccountChange((user) => {
    $('nav-mine').hidden = !user;
    state.list = { kind: 'recent' };
    $<HTMLTextAreaElement>('note-text').value = '';
    toggle.setAttribute('aria-pressed', 'false');
    void loadCollections();
    paperShown(state.paper);
    if (ctx.view === 'mine') void renderList();
  });
}
