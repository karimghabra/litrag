/**
 * The Projects tab: every project (one library each) as a card with what it holds — papers by
 * format and type, how far they are read, the candidates its searches found, the passages
 * embedded — and the two things done to projects as a whole: making one, and merging several
 * into a new one.
 */

import { me, session } from './account.ts';
import { askText } from './dialog.ts';
import { $, activity, ctx, el, fmtInt, log, onWorkerEvent, queryText, rejectionText, request, setProject, showView, type ProjectSummary } from './shared.ts';

const TYPE_COLORS: Record<string, string> = {
  research: 'var(--methods)', review: 'var(--abstract)', letter: 'var(--results)', editorial: 'var(--discussion)',
  'case-report': 'var(--results-discussion)', protocol: 'var(--accent)', data: 'var(--introduction)', correction: 'var(--back)', other: 'var(--other)',
};

let reload: (() => Promise<void>) | null = null;

export function initProjects(loadAll: () => Promise<void>): void {
  reload = loadAll;
  const form = $<HTMLFormElement>('new-project');
  $('new-library').addEventListener('click', () => {
    form.hidden = false;
    $('merge-form').hidden = true;
    $<HTMLInputElement>('np-name').focus();
  });
  $('np-cancel').addEventListener('click', () => (form.hidden = true));
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    void createProject();
  });
  const merge = $<HTMLFormElement>('merge-form');
  $('merge-open').addEventListener('click', () => {
    renderMergeSources();
    merge.hidden = false;
    form.hidden = true;
  });
  $('merge-cancel').addEventListener('click', () => (merge.hidden = true));
  merge.addEventListener('submit', (e) => {
    e.preventDefault();
    void mergeProjects();
  });
  $('migrate-plan').addEventListener('click', () => void planMove());
  $('projects-mine').addEventListener('change', () => renderProjects());
  $('migrate-go').addEventListener('click', () => void move());
  onWorkerEvent((ev) => {
    if (ev['event'] === 'done' && ev['op'] === 'merge') {
      activity.hide();
      const target = String(ev['target'] ?? '');
      // a PDF a source keeps beside an XML paper the target held without one: kept there, or not
      const kept = (ev['figures'] as unknown[] | undefined)?.length ?? 0;
      const notKept = (ev['figures_not_kept'] as unknown[] | undefined)?.length ?? 0;
      const figures = kept || notKept ? `, ${kept} PDF${kept === 1 ? '' : 's'} kept beside an XML paper for its figures, ${notKept} not kept` : '';
      log('stage', `Merged into ${target}: ${ev['filed']} papers filed, ${ev['duplicates']} already there, ${(ev['rebuilt'] as unknown[] | undefined)?.length ?? 0} rebuilt${figures}`);
      void reload?.().then(() => {
        if (target) setProject(target);
      });
    }
    if (ev['event'] === 'done' && ev['op'] === 'migrate') {
      activity.hide();
      const moved = (ev['moved'] as { id: string; new: number; held: number; skipped?: { paper: string; reason: string }[] }[] | undefined) ?? [];
      for (const m of moved) {
        log('stage', `Moved ${m.id} into the shared store: ${m.new} papers new there, ${m.held} held there already`);
        for (const s of m.skipped ?? []) log('failed', `Not moved from ${m.id}: ${s.paper} — ${s.reason}`);
      }
      if (ev['backup']) log('stage', `Backup of the stores as they were: ${String(ev['backup'])}`);
      $('migrate-go').hidden = true;
      void reload?.();
    }
  });
}

async function createProject(): Promise<void> {
  const name = $<HTMLInputElement>('np-name').value.trim();
  const description = $<HTMLTextAreaElement>('np-description').value.trim();
  if (!name) return;
  try {
    // made while signed in, the project is this account's own: only it renames or deletes it
    const r = await request<{ library: { id: string } }>('init', { name, description, session: session() });
    $<HTMLFormElement>('new-project').hidden = true;
    $<HTMLInputElement>('np-name').value = '';
    $<HTMLTextAreaElement>('np-description').value = '';
    log('stage', `Created project ${r.library.id}`);
    await reload?.();
    setProject(r.library.id);
  } catch (e) {
    log('error', (e as Error).message);
    window.alert((e as Error).message);
  }
}

function renderMergeSources(): void {
  const box = $('merge-sources');
  box.innerHTML = '';
  for (const p of ctx.projects) {
    const label = el('label');
    const cb = el('input') as HTMLInputElement;
    cb.type = 'checkbox';
    cb.value = p.id;
    label.append(cb, document.createTextNode(`${p.name} (${p.counts?.papers ?? 0})`));
    box.append(label);
  }
}

async function mergeProjects(): Promise<void> {
  const sources = [...document.querySelectorAll<HTMLInputElement>('#merge-sources input:checked')].map((c) => c.value);
  const name = $<HTMLInputElement>('merge-name').value.trim();
  if (sources.length < 2 || !name) {
    window.alert('Choose two projects or more, and name the merged one.');
    return;
  }
  try {
    activity.show(`Merging ${sources.length} libraries`);
    await request('merge', { sources, name, session: session() });
    $<HTMLFormElement>('merge-form').hidden = true;
    log('stage', `Merging ${sources.join(', ')} into “${name}”`);
  } catch (e) {
    activity.hide();
    log('error', (e as Error).message);
    window.alert((e as Error).message);
  }
}

interface MovePlan {
  libraries: { id: string; name: string; papers: number; new: number; held: number; candidates: number; vectors: number; files: number; bytes: number }[];
  totals: { papers: number; new: number; held: number; candidates: number; vectors: number; files: number; bytes: number };
}

/** The projects that still keep their own store, as the `projects` op names them (`own_store`): the band that offers to move them. */
export function renderMoveBand(own: string[]): void {
  const band = $('migrate-band');
  band.hidden = own.length === 0;
  if (!own.length) return;
  const names = own.map((id) => ctx.projects.find((p) => p.id === id)?.name ?? id);
  $('migrate-text').textContent =
    `${names.join(', ')} ${own.length === 1 ? 'keeps its' : 'keep their'} own store. In the shared store every paper is kept once, ` +
    'whichever projects hold it, and a query can ask several projects at once. A move copies every paper with its reading, ' +
    'its passages and its candidates (nothing is read again), after a backup, and leaves the old stores where they are.';
  $('migrate-go').hidden = true;
}

/** Says what a move would do (`migrate` with `dry_run`), writing nothing; then offers it. */
async function planMove(): Promise<void> {
  try {
    const r = await request<{ plan: MovePlan }>('migrate', { dry_run: true });
    const t = r.plan.totals;
    const mb = (t.bytes / 1e6).toFixed(1);
    $('migrate-text').textContent =
      `${fmtInt(t.papers)} papers in ${r.plan.libraries.length} project${r.plan.libraries.length === 1 ? '' : 's'}: ` +
      `${fmtInt(t.new)} new to the shared store, ${fmtInt(t.held)} held there already (kept as they were read there); ` +
      `${fmtInt(t.candidates)} candidates, ${fmtInt(t.vectors)} passages embedded, ${fmtInt(t.files)} files (${mb} MB) to copy. ` +
      'A backup of every store is made first; the old stores are left where they are.';
    $('migrate-go').hidden = r.plan.libraries.length === 0;
  } catch (e) {
    log('error', (e as Error).message);
  }
}

async function move(): Promise<void> {
  try {
    activity.show('Moving projects into the shared store');
    await request('migrate', {});
    $('migrate-go').hidden = true;
  } catch (e) {
    activity.hide();
    log('error', (e as Error).message);
    window.alert((e as Error).message);
  }
}

export function renderProjects(): void {
  const box = $('projects');
  box.innerHTML = '';
  $('projects-count').textContent = ctx.projects.length ? `${ctx.projects.length}` : '';
  if (!ctx.projects.length) {
    box.append(el('div', 'empty', 'No projects yet. Make one: a name, and a line on what its literature is about.'));
    return;
  }
  const onlyMine = $<HTMLInputElement>('projects-mine').checked;
  const shown = ctx.projects.filter((p) => !onlyMine || isMine(p));
  if (!shown.length) box.append(el('div', 'empty', 'You have made no project yet. Every project here is yours to read; the ones you make are yours to rename and delete.'));
  for (const p of shown) box.append(card(p));
}

/** A project the signed-in account made. */
export function isMine(p: ProjectSummary): boolean {
  const user = me();
  return Boolean(user && p.owner && p.owner === user.id);
}

/** Whether the signed-in account may rename, describe and delete a project: its own, or one no account made (from before accounts). */
export function mayChange(p: ProjectSummary): boolean {
  return !p.owner || isMine(p);
}

async function deleteProject(p: ProjectSummary): Promise<void> {
  if (!window.confirm(`Delete the project “${p.name}”? Its papers stay in the shared store, and in every other project that holds them; its folder is moved aside, not erased.`)) return;
  try {
    await request('delete_project', { lib: p.id, session: session() });
    log('stage', `Deleted the project ${p.name}`);
    if (ctx.lib === p.id) setProject(null);
    await reload?.();
  } catch (e) {
    window.alert(rejectionText(e));
  }
}

function card(p: ProjectSummary): HTMLElement {
  const c = el('div', `card project${p.id === ctx.lib ? ' current' : ''}`);
  c.dataset['id'] = p.id;
  const name = el('div', 'name', p.name);
  if (p.store === 'own') name.append(el('span', 'tag', 'own store'));
  if (isMine(p)) name.append(el('span', 'tag mine', 'yours'));
  c.append(name);
  c.append(el('div', `desc${p.description ? '' : ' none'}`, p.description || 'No description yet.'));
  const n = p.counts ?? {};
  const stats = el('div', 'stats');
  const stat = (label: string, v: number | undefined) => {
    const s = el('span');
    s.append(el('b', undefined, fmtInt(v ?? 0)), document.createTextNode(` ${label}`));
    stats.append(s);
  };
  stat('papers', n.papers);
  stat('read', n.parsed);
  if (n.failed) stat('failed', n.failed);
  stat('XML', n.xml);
  stat('PDF', n.pdf);
  stat('passages embedded', n.vectors);
  const cands = n.candidates ?? {};
  const candTotal = Object.values(cands).reduce((a, b) => a + b, 0);
  if (candTotal) stat('candidates', candTotal);
  if (cands['needs-pdf']) stat('need a PDF', cands['needs-pdf']);
  c.append(stats);
  const types = Object.entries(n.by_type ?? {}).sort((a, b) => b[1] - a[1]);
  const total = types.reduce((a, [, k]) => a + k, 0);
  if (total) {
    const stack = el('div', 'stack');
    const legend = el('div', 'legend');
    for (const [t, k] of types) {
      const seg = el('span');
      seg.style.width = `${(100 * k) / total}%`;
      seg.style.background = TYPE_COLORS[t] ?? 'var(--other)';
      seg.title = `${t}: ${k}`;
      stack.append(seg);
      const item = el('span');
      const sw = el('i');
      sw.style.background = TYPE_COLORS[t] ?? 'var(--other)';
      item.append(sw, document.createTextNode(`${t} ${k}`));
      legend.append(item);
    }
    c.append(stack, legend);
  }
  if (p.queries?.length) c.append(el('div', 'muted', `${p.queries.length} search${p.queries.length === 1 ? '' : 'es'} · last: ${queryText(p.queries[p.queries.length - 1]!)}`));
  const actions = el('div', 'actions');
  const go = (label: string, view: string, primary = false) => {
    const b = el('button', primary ? 'primary small' : 'ghost small', label);
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      setProject(p.id);
      showView(view);
    });
    actions.append(b);
  };
  go('Papers', 'papers', true);
  go('Search', 'search');
  go('Types', 'types');
  go('Query', 'query');
  if (mayChange(p)) {
    const edit = el('button', 'ghost small', 'Describe…');
    edit.addEventListener('click', async (e) => {
      e.stopPropagation();
      const d = await askText(`What is “${p.name}” about?`, { value: p.description ?? '', long: true, ok: 'Save' });
      if (d === null) return;
      try {
        await request('describe', { lib: p.id, description: d.trim(), session: session() });
        await reload?.();
      } catch (err) {
        log('error', rejectionText(err));
      }
    });
    const rename = el('button', 'ghost small', 'Rename…');
    rename.addEventListener('click', async (e) => {
      e.stopPropagation();
      const n = await askText(`Rename “${p.name}”`, { value: p.name, ok: 'Rename' });
      if (!n?.trim() || n.trim() === p.name) return;
      try {
        await request('describe', { lib: p.id, name: n.trim(), session: session() });
        await reload?.();
      } catch (err) {
        window.alert(rejectionText(err));
      }
    });
    actions.append(edit, rename);
    if (p.store !== 'own') {
      const del = el('button', 'ghost small', 'Delete…');
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        void deleteProject(p);
      });
      actions.append(del);
    }
  }
  c.append(actions);
  c.addEventListener('click', () => {
    setProject(p.id);
    showView('papers');
  });
  return c;
}
