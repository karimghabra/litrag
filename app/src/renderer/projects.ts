/**
 * The Projects tab: every project (one library each) as a card with what it holds — papers by
 * format and type, how far they are read, the candidates its searches found, the passages
 * embedded — and the two things done to projects as a whole: making one, and merging several
 * into a new one.
 */

import { $, activity, ctx, el, fmtInt, log, onWorkerEvent, queryText, request, setProject, showView, type ProjectSummary } from './shared.ts';

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
  onWorkerEvent((ev) => {
    if (ev['event'] === 'done' && ev['op'] === 'merge') {
      activity.hide();
      const target = String(ev['target'] ?? '');
      log('stage', `Merged into ${target}: ${ev['filed']} papers filed, ${ev['duplicates']} already there, ${(ev['rebuilt'] as unknown[] | undefined)?.length ?? 0} rebuilt`);
      void reload?.().then(() => {
        if (target) setProject(target);
      });
    }
  });
}

async function createProject(): Promise<void> {
  const name = $<HTMLInputElement>('np-name').value.trim();
  const description = $<HTMLTextAreaElement>('np-description').value.trim();
  if (!name) return;
  try {
    const r = await request<{ library: { id: string } }>('init', { name, description });
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
    await request('merge', { sources, name });
    $<HTMLFormElement>('merge-form').hidden = true;
    log('stage', `Merging ${sources.join(', ')} into “${name}”`);
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
  for (const p of ctx.projects) box.append(card(p));
}

function card(p: ProjectSummary): HTMLElement {
  const c = el('div', `card project${p.id === ctx.lib ? ' current' : ''}`);
  c.dataset['id'] = p.id;
  c.append(el('div', 'name', p.name));
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
  const edit = el('button', 'ghost small', 'Describe…');
  edit.addEventListener('click', async (e) => {
    e.stopPropagation();
    const d = window.prompt(`What is “${p.name}” about?`, p.description ?? '');
    if (d === null) return;
    try {
      await request('describe', { lib: p.id, description: d.trim() });
      await reload?.();
    } catch (err) {
      log('error', (err as Error).message);
    }
  });
  actions.append(edit);
  c.append(actions);
  c.addEventListener('click', () => {
    setProject(p.id);
    showView('papers');
  });
  return c;
}
