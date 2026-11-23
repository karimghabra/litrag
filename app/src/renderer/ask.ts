/**
 * The Ask tab: a question answered by the local model from the project's passages, every
 * sentence closed by the passages it rests on. A citation is a button: it opens the paper at
 * that passage in the Papers tab, on its page with its box. What the worker's check found is
 * shown, not hidden: a sentence whose numbers or words are not in the passage it cites is
 * marked to be checked, sentences that cited nothing are counted as left out, and a question
 * the library does not answer says so. The passages the model was given are listed under the
 * answer, the cited ones first.
 */

import { $, chooseProject, ctx, el, hooks, log, onProjectChange, onViewShown, onWorkerEvent, projectName, request, roleColor } from './shared.ts';

export interface AskSource {
  n: number;
  lib: string;
  node_id: string;
  paper: string;
  title?: string | null;
  year?: string | null;
  journal?: string | null;
  doi?: string | null;
  section?: string | null;
  ancestry?: string[];
  role?: string | null;
  page?: number | null;
  text: string;
  cited?: boolean;
}

export interface AskClaim {
  text: string;
  sources: number[];
  support: 'supported' | 'weak';
  numbers_missing?: string[];
  words?: number;
}

export interface AskAnswer {
  question: string;
  libs: string[];
  model: string;
  status: 'answered' | 'partial' | 'not_found' | 'unanswered';
  summary: string;
  claims: AskClaim[];
  dropped: { text: string; reason: string }[];
  missing: string;
  sources: AskSource[];
  timing?: { total?: number; model?: number; tokens_per_second?: number | null; prompt_tokens?: number | null; tokens?: number | null };
  error?: string;
  warning?: string;
}

export interface Models {
  default: string;
  down: boolean;
  error?: string | null;
  models: { name: string; parameters?: string | null; size?: number | null }[];
}

const STATUS: Record<AskAnswer['status'], [string, string]> = {
  answered: ['Answered from the library', 'ok'],
  partial: ['Partly answered: the library leaves some of it out', 'warn'],
  not_found: ['Not in the library', 'bad'],
  unanswered: ['No answer from the model', 'bad'],
};

export function statusLine(a: Pick<AskAnswer, 'status'>): [string, string] {
  return STATUS[a.status] ?? [a.status, ''];
}

/** A passage's place as a citation reads it: paper (year) — section path — page. */
export function citationText(s: AskSource): string {
  const path = (s.ancestry ?? []).filter(Boolean).join(' › ') || s.section || '';
  return [`${s.title || s.paper}${s.year ? ` (${s.year})` : ''}`, path, s.page ? `p. ${s.page}` : ''].filter(Boolean).join(' — ');
}

/** Why a claim is marked to be checked, in words a reader acts on; empty for a supported one. */
export function weakReason(c: AskClaim): string {
  if (c.support !== 'weak') return '';
  if (c.numbers_missing?.length) return `${c.numbers_missing.join(', ')} ${c.numbers_missing.length === 1 ? 'is' : 'are'} not printed in the passage it cites: check it there`;
  return 'most of its words are not in the passage it cites: check it there';
}

/** The answer's footing in one line: the model, the time, the speed, what was left out. */
export function metaLine(a: AskAnswer): string[] {
  const out = [`${a.model}`];
  if (a.timing?.total !== undefined) out.push(`${a.timing.total.toFixed(1)} s`);
  if (a.timing?.tokens_per_second) out.push(`${a.timing.tokens_per_second} tokens/s`);
  const papers = new Set(a.sources.map((s) => `${s.lib}/${s.paper}`)).size;
  out.push(`${a.sources.length} passage${a.sources.length === 1 ? '' : 's'} from ${papers} paper${papers === 1 ? '' : 's'}`);
  if (a.libs.length > 1) out.push(`${a.libs.length} projects`);
  const weak = a.claims.filter((c) => c.support === 'weak').length;
  if (weak) out.push(`${weak} sentence${weak === 1 ? '' : 's'} to check`);
  if (a.dropped.length) out.push(`${a.dropped.length} sentence${a.dropped.length === 1 ? '' : 's'} left out: ${a.dropped.length === 1 ? 'it cited' : 'they cited'} no passage given`);
  if (a.warning) out.push(a.warning);
  return out;
}

/** The passages, the cited ones first, each group in the order the model was given them. */
export function orderedSources(a: AskAnswer): AskSource[] {
  return [...a.sources].sort((x, y) => Number(!!y.cited) - Number(!!x.cited) || x.n - y.n);
}

/** A model's name as Ollama lists it: one given without a tag is its `latest`. */
function tagged(name: string): string {
  return name.includes(':') ? name : `${name}:latest`;
}

/**
 * The Model picker's entries and the one selected: the models Ollama holds, the default first
 * when it is not among them, under the name Ollama lists it by when it is. The default is
 * selected unless the person chose another that is still listed.
 */
export function modelChoices(r: Pick<Models, 'default' | 'models'>, was: string, chosen: boolean): { names: string[]; default: string; held: boolean; selected: string } {
  const listed = r.models.find((m) => tagged(m.name) === tagged(r.default))?.name;
  const dflt = listed ?? r.default;
  const names = r.models.map((m) => m.name);
  if (listed === undefined) names.unshift(dflt);
  return { names, default: dflt, held: listed !== undefined, selected: chosen && names.includes(was) ? was : dflt };
}

/** The number of passages asked for: what the field says, held to 1..20; 8 when it says nothing. */
export function passagesAsked(field: string): number {
  const n = Number(field);
  return field.trim() === '' || !Number.isFinite(n) ? 8 : Math.max(1, Math.min(20, Math.trunc(n)));
}

let pending = false;
let modelChosen = false;

export function initAsk(): void {
  $<HTMLFormElement>('ask-form').addEventListener('submit', (e) => {
    e.preventDefault();
    void ask();
  });
  $<HTMLTextAreaElement>('ask-q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void ask();
    }
  });
  $<HTMLSelectElement>('ask-model').addEventListener('change', () => {
    modelChosen = true;
  });
  onProjectChange(() => {
    $('ask-project').textContent = projectName();
    if (ctx.view === 'ask') void loadModels();
  });
  onViewShown((v) => {
    if (v === 'ask') {
      $('ask-project').textContent = projectName();
      void loadModels();
    }
  });
  onWorkerEvent((ev) => {
    if (ev['event'] === 'asking' && pending) $('ask-progress').textContent = String(ev['message'] ?? '');
  });
}

async function loadModels(): Promise<void> {
  const sel = $<HTMLSelectElement>('ask-model');
  const was = sel.value;
  try {
    const r = await request<Models>('models', ctx.lib ? { lib: ctx.lib } : {});
    sel.innerHTML = '';
    const pick = modelChoices(r, was, modelChosen);
    for (const name of pick.names) {
      const m = r.models.find((x) => x.name === name);
      const label = `${name}${m?.parameters ? ` · ${m.parameters}` : ''}${name === pick.default ? ' · default' : ''}${m ? '' : ' · not pulled'}`;
      const o = el('option', undefined, label) as HTMLOptionElement;
      o.value = name;
      sel.append(o);
    }
    sel.value = pick.selected;
    $('ask-models').textContent = r.down ? r.error ?? 'Ollama is not answering' : pick.held ? '' : `${r.default} is not pulled: ollama pull ${r.default}`;
  } catch (e) {
    $('ask-models').textContent = `models: ${(e as Error).message}`;
  }
}

async function ask(): Promise<void> {
  if (!ctx.lib) {
    window.alert('Choose a project first.');
    return;
  }
  const question = $<HTMLTextAreaElement>('ask-q').value.trim();
  if (!question || pending) return;
  const k = passagesAsked($<HTMLInputElement>('ask-k').value);
  const scope = $<HTMLSelectElement>('ask-scope').value;
  const model = $<HTMLSelectElement>('ask-model').value || undefined;
  const box = $('ask-answer');
  box.innerHTML = '';
  $('ask-sources').innerHTML = '';
  $('ask-meta').innerHTML = '';
  $('ask-progress').textContent = 'Retrieving passages…';
  pending = true;
  $<HTMLButtonElement>('ask-go').disabled = true;
  const lib = ctx.lib;
  try {
    const r = await request<AskAnswer>('ask', { ...(scope === 'all' ? { libs: ['all'] } : { lib }), question, k, model });
    if (ctx.lib !== lib && scope !== 'all') return;
    renderAnswer(r);
  } catch (e) {
    box.append(el('div', 'empty', `The question failed: ${(e as Error).message}`));
    log('error', `ask: ${(e as Error).message}`);
  } finally {
    pending = false;
    $<HTMLButtonElement>('ask-go').disabled = false;
    $('ask-progress').textContent = '';
  }
}

/** Open a cited passage: in the Papers tab, on its page with its box — switching project first
 *  when the passage came from another one, which asks about a Plots picture not saved. */
async function openSource(s: AskSource): Promise<void> {
  if (s.lib && !(await chooseProject(s.lib))) return;
  hooks.openPaper(s.paper, s.node_id);
}

function cite(n: number, byN: Map<number, AskSource>): HTMLElement {
  const s = byN.get(n);
  const b = el('button', 'cite', String(n)) as HTMLButtonElement;
  b.type = 'button';
  b.dataset['n'] = String(n);
  if (s) {
    b.dataset['node'] = s.node_id;
    b.title = `${citationText(s)}\nOpen the paper at this passage`;
    b.addEventListener('click', () => void openSource(s));
  }
  return b;
}

export function renderAnswer(a: AskAnswer): void {
  const [label, kind] = statusLine(a);
  const meta = $('ask-meta');
  meta.innerHTML = '';
  meta.append(el('span', `ask-status ${kind}`, label));
  for (const m of metaLine(a)) meta.append(el('span', undefined, m));
  const box = $('ask-answer');
  box.innerHTML = '';
  box.dataset['status'] = a.status;
  const byN = new Map(a.sources.map((s) => [s.n, s]));
  const prose = el('div', 'ask-prose');
  if (a.claims.length) {
    for (const c of a.claims) {
      const span = el('span', `claim${c.support === 'weak' ? ' weak' : ''}`);
      span.append(document.createTextNode(c.text.replace(/[.!?]\s*$/, '')));
      const cites = el('span', 'cites');
      for (const n of c.sources) cites.append(cite(n, byN));
      span.append(' ', cites, '. ');
      const why = weakReason(c);
      if (why) span.title = why;
      prose.append(span);
    }
  } else {
    prose.append(el('p', 'ask-none', a.error ? `No answer: ${a.error}.` : 'The library does not answer this.'));
  }
  box.append(prose);
  if (a.missing && a.status !== 'answered') box.append(el('div', 'ask-missing', `Not in the passages: ${a.missing}`));
  if (a.status === 'unanswered' && a.sources.length) box.append(el('div', 'muted', 'The passages retrieved are below; open one to read it in its paper.'));

  const list = $('ask-sources');
  list.innerHTML = '';
  if (!a.sources.length) return;
  list.append(el('div', 'side-h', `Passages given to the model (${a.sources.length})`));
  for (const s of orderedSources(a)) {
    const card = el('div', `ask-source${s.cited ? ' cited' : ''}`);
    card.dataset['n'] = String(s.n);
    card.dataset['node'] = s.node_id;
    const head = el('div', 'ask-source-head');
    head.append(el('span', 'n', `[${s.n}]`), el('span', 'ptitle', `${s.title || s.paper}${s.year ? ` (${s.year})` : ''}`));
    if (a.libs.length > 1) head.append(el('span', 'pill', projectName(s.lib)));
    card.append(head);
    const where = el('div', 'crumb');
    where.append(el('span', undefined, [(s.ancestry ?? []).filter(Boolean).join(' › ') || s.section || '', s.page ? `p. ${s.page}` : ''].filter(Boolean).join(' · ')));
    if (s.role) {
      const r = el('span', undefined, ` · ${s.role}`);
      r.style.color = roleColor(s.role);
      where.append(r);
    }
    card.append(where);
    card.append(el('div', 'ask-source-text', s.text.length > 900 ? `${s.text.slice(0, 900)}…` : s.text));
    const foot = el('div', 'qhit-foot');
    const open = el('button', 'ghost small', 'Open at this passage');
    open.addEventListener('click', () => void openSource(s));
    foot.append(open);
    if (s.doi) foot.append(el('span', 'muted', `doi:${s.doi}`));
    card.append(foot);
    list.append(card);
  }
}
