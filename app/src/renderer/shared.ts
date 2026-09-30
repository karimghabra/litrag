/**
 * What every view shares: the current project, the worker's requests and events, the log,
 * the status light, the activity bar and the tabs. The views hold nothing the worker did not
 * return; this module holds only which project and which tab are chosen.
 */

import type { LitragApi } from '../main/preload.ts';

declare global {
  interface Window {
    litrag: LitragApi;
  }
}

export const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

export const el = (tag: string, cls?: string, text?: string) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}

export const ROLES = ['abstract', 'introduction', 'methods', 'results', 'results-discussion', 'discussion', 'other', 'references', 'back'];
export const roleColor = (role: string) => `var(--${ROLES.includes(role) ? role : 'other'})`;
export const MECHANISMS = ['vocabulary', 'catalogue', 'embedder', 'built', 'outline', 'inherited', 'other', 'unknown'];
/** A mechanism's colour; the few that are one of the others under another name share its colour. */
export const mechColor = (m: string) => {
  const as = ({ none: 'other', withheld: 'other', disagreement: 'other', position: 'vocabulary', numbered: 'vocabulary', inherited: 'unknown', front: 'unknown', untitled: 'built' } as Record<string, string>)[m] ?? m;
  return `var(--m-${['vocabulary', 'catalogue', 'embedder', 'built', 'outline', 'other'].includes(as) ? as : 'unknown'})`;
};

export async function request<T = Record<string, unknown>>(op: string, params: Record<string, unknown> = {}): Promise<T> {
  return (await window.litrag.request(op, params)) as unknown as T;
}

// ---- the current project -------------------------------------------------------------------

export interface ProjectSummary {
  id: string;
  name: string;
  description?: string | null;
  createdAt?: string | null;
  /** a search is `{query, at, total, added}`; a library from before the studio kept only the query */
  queries?: (string | { query: string; at?: string; total?: number; added?: number })[];
  counts?: {
    papers?: number;
    parsed?: number;
    failed?: number;
    pdf?: number;
    xml?: number;
    by_type?: Record<string, number>;
    nodes?: number;
    candidates?: Record<string, number>;
    vectors?: number;
  };
}

export const ctx = {
  lib: null as string | null,
  projects: [] as ProjectSummary[],
  view: 'papers' as string,
};

const projectListeners: ((lib: string | null) => void)[] = [];
export function onProjectChange(fn: (lib: string | null) => void): void {
  projectListeners.push(fn);
}

export function setProject(id: string | null): void {
  if (ctx.lib === id) return;
  ctx.lib = id;
  const sel = $<HTMLSelectElement>('library');
  if (sel && id) sel.value = id;
  try {
    if (id) window.localStorage.setItem('litrag.project', id);
  } catch {
    // a convenience only
  }
  for (const fn of projectListeners) fn(id);
}

export function rememberedProject(): string | null {
  try {
    return window.localStorage.getItem('litrag.project');
  } catch {
    return null;
  }
}

export const queryText = (q: string | { query: string }): string => (typeof q === 'string' ? q : q.query);

export function projectName(id: string | null = ctx.lib): string {
  return ctx.projects.find((p) => p.id === id)?.name ?? id ?? '';
}

// ---- tabs --------------------------------------------------------------------------------------

const viewListeners: ((view: string) => void)[] = [];
export function onViewShown(fn: (view: string) => void): void {
  viewListeners.push(fn);
}

export function showView(view: string): void {
  ctx.view = view;
  for (const b of document.querySelectorAll<HTMLButtonElement>('#nav button')) b.classList.toggle('on', b.dataset['view'] === view);
  for (const s of document.querySelectorAll<HTMLElement>('#views > .view')) s.hidden = s.id !== `view-${view}`;
  try {
    window.localStorage.setItem('litrag.view', view);
  } catch {
    // a convenience only
  }
  for (const fn of viewListeners) fn(view);
}

// ---- worker events, fanned out -------------------------------------------------------------------

const eventListeners: ((ev: Record<string, unknown>) => void)[] = [];
export function onWorkerEvent(fn: (ev: Record<string, unknown>) => void): void {
  eventListeners.push(fn);
}
export function dispatch(ev: Record<string, unknown>): void {
  for (const fn of eventListeners) {
    try {
      fn(ev);
    } catch (e) {
      log('error', `event ${String(ev['event'])}: ${(e as Error).message}`);
    }
  }
}

// ---- status, log, activity ------------------------------------------------------------------------

export function setStatus(kind: 'ok' | 'busy' | 'bad' | '', text: string) {
  const s = $('worker-status');
  s.className = `status ${kind}`;
  s.querySelector('.text')!.textContent = text;
  s.title = text;
}

/** The worker is not running, and why — nothing to run, it would not start, it exited — in the
 *  status and, in full, in the band under the header, where a person sees it without the log. */
export function showWorkerProblem(message: string) {
  setStatus('bad', 'worker not running');
  $('worker-status').title = message;
  const band = $('worker-problem');
  band.querySelector('.problem-text')!.textContent = message;
  band.hidden = false;
}

/** A request's rejection as the main process gave it, without Electron's "Error invoking remote method" wrapping. */
export function rejectionText(e: unknown): string {
  return String((e as Error)?.message ?? e).replace(/^Error invoking remote method '[^']*': (?:Error: )?/, '');
}

export function log(kind: string, text: string, paper?: string) {
  const line = el('div', `log-line ${kind}`);
  line.append(el('span', 't', new Date().toLocaleTimeString()));
  if (paper) line.append(el('span', 'paper', paper));
  line.append(el('span', 'm', text));
  const box = $('log');
  box.append(line);
  while (box.childElementCount > 400) box.firstElementChild?.remove();
  box.scrollTop = box.scrollHeight;
}

/** The bar in the header: what the worker is working through — papers read, passages embedded,
 *  candidates fetched — and how far it has got. Driven by the worker's `progress` events. */
export const activity = {
  show(label: string, done?: number, total?: number) {
    const a = $('activity');
    a.hidden = false;
    a.querySelector('.activity-label')!.textContent = label;
    const known = total !== undefined && total > 0 && done !== undefined;
    a.classList.toggle('indeterminate', !known);
    (a.querySelector('.activity-fill') as HTMLElement).style.width = known ? `${Math.min(100, (100 * done!) / total!)}%` : '40%';
    a.querySelector('.activity-count')!.textContent = known ? `${done} / ${total}` : '';
    a.dataset['op'] = label;
  },
  hide() {
    $('activity').hidden = true;
  },
};

/** Set by the Papers view: open a paper there, and a node of it when one is named. */
export const hooks = {
  openPaper: (_key: string, _node?: string): void => undefined,
};

export function fmtInt(n: number | null | undefined): string {
  return n === null || n === undefined ? '–' : n.toLocaleString();
}

export function words(s: string): number {
  return s.split(/\s+/).filter(Boolean).length;
}
