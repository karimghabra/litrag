/**
 * The labelling panel: a person says which of a paper's methods subsections a finding was
 * measured by, or that none is in the paper — the truth the `measured_by` edges are measured
 * against (parser/litrag_parser/truth.py). It steps through the worker's `label_queue`, or
 * opens on one finding from the node detail's "Measured by" box. Each candidate starts checked
 * when the finding's saved labels say so, else when the linker drew an edge to it; expanding one
 * lists its paragraphs, so the paragraph the finding rests on can be marked too. Number keys
 * toggle the candidates, N "no method", Enter saves and moves on, S skips, Esc closes.
 */

import { $, activity, ctx, el, hooks, log, onWorkerEvent, request } from './shared.ts';
import { candidateName, initialChoice, keyIndex, keyLabel, LABEL_RULE, labelsOf, markParagraph, modelLine, toggle, toggleNone, truthLine, type Choice, type LabelItem, type Truth } from './truth.ts';

const panel = {
  items: [] as LabelItem[],
  at: 0,
  /** the library the queue came from: another project chosen meanwhile starts a new one */
  lib: null as string | null,
  choice: null as Choice | null,
  expanded: new Set<string>(),
  /** opened on one finding from the node detail: saving closes it */
  single: false,
  /** where the queue was when a single finding was opened over it */
  resume: null as { items: LabelItem[]; at: number } | null,
  onClose: null as (() => void) | null,
  saving: false,
  /** the model labelled findings since the queue was drawn: the next step draws again, its findings first */
  stale: false,
};

const open = (): boolean => !$('label-panel').hidden;

/** Open the panel: on one finding, or on the queue — where it was left, or freshly drawn. */
export async function openLabelling(opts: { finding?: string; onClose?: () => void } = {}): Promise<void> {
  const lib = ctx.lib;
  if (!lib) return;
  $('label-error').textContent = '';
  panel.onClose = opts.onClose ?? null;
  if (opts.finding) {
    const r = await request<{ items: LabelItem[] }>('label_queue', { lib, finding: opts.finding });
    if (!panel.single && panel.lib === lib) panel.resume = { items: panel.items, at: panel.at };
    panel.items = r.items;
    panel.at = 0;
    panel.single = true;
  } else if (panel.single || panel.lib !== lib || panel.at >= panel.items.length) {
    if (panel.single && panel.resume && panel.lib === lib) {
      ({ items: panel.items, at: panel.at } = panel.resume);
      panel.resume = null;
    } else panel.items = [];
    panel.single = false;
    if (panel.at >= panel.items.length) await draw(lib);
  }
  panel.lib = lib;
  $('label-panel').hidden = false;
  $('label-skip').hidden = panel.single;
  $('label-save').textContent = panel.single ? 'Save' : 'Save & next';
  show();
  // the keys are the panel's now, not the button behind it that opened it
  document.querySelector<HTMLElement>('#label-panel .label-card')?.focus();
  void refreshTruth();
}

/** A fresh queue: the findings not yet labelled, spread across papers and publishers. */
async function draw(lib: string): Promise<void> {
  const r = await request<{ items: LabelItem[] }>('label_queue', { lib, n: 100 });
  panel.items = r.items;
  panel.at = 0;
}

function close(): void {
  $('label-panel').hidden = true;
  const after = panel.onClose;
  panel.onClose = null;
  after?.();
}

function item(): LabelItem | null {
  return panel.items[panel.at] ?? null;
}

/** The current finding, from scratch: its paper and place, its text, the candidates as the choice has them. */
function show(): void {
  const it = item();
  panel.choice = it ? initialChoice(it) : null;
  panel.expanded = new Set(it ? [...(panel.choice?.paragraph.keys() ?? [])] : []);
  $('label-pos').textContent = it ? (panel.single ? 'one finding' : `${panel.at + 1} of ${panel.items.length} in the queue`) : '';
  $<HTMLButtonElement>('label-save').disabled = !it;
  const body = $('label-body');
  body.innerHTML = '';
  if (!it) {
    body.append(el('div', 'empty', 'Nothing left to label in this project: every finding the queue would offer has its labels.'));
    return;
  }
  const head = el('div', 'label-paper');
  head.append(el('span', 'ptitle', it.title));
  head.append(el('span', 'pill', it.prefix === 'no-doi' ? 'no DOI' : it.prefix));
  const ev = el('span', `ev ev-${it.evidence}`, it.evidence === 'unlinked' ? 'unlinked' : `linked by ${it.evidence}`);
  ev.title = 'what linked this finding to a method, if anything did';
  head.append(ev);
  const go = el('button', 'ghost small', 'Open in the tree');
  go.title = 'Close the panel and show this finding in its paper; Label links comes back to it';
  go.addEventListener('click', () => {
    close();
    hooks.openPaper(it.paper, it.finding.node_id);
  });
  head.append(go);
  body.append(head);
  const crumbs = el('div', 'crumbs');
  crumbs.textContent = [...it.finding.ancestry, it.finding.page ? `p. ${it.finding.page}` : ''].filter(Boolean).join(' › ') || 'top level';
  body.append(crumbs);
  body.append(el('div', 'label-finding', it.finding.text));
  if (it.labels.length) body.append(el('div', 'muted label-note', `Labelled before${it.labels[0]?.by ? ` by ${it.labels[0].by}` : ''}${it.labels[0]?.at ? `, ${it.labels[0].at.slice(0, 10)}` : ''}: saving replaces it.`));
  body.append(el('div', 'label-q', 'Which of the paper’s methods was this finding measured by?'));
  body.append(el('div', 'muted label-rule', LABEL_RULE));
  body.append(el('div', 'label-cands'));
  renderCandidates();
}

/** The candidates and "no method in this paper", drawn again whenever the choice changes. */
function renderCandidates(): void {
  const it = item();
  const box = document.querySelector<HTMLElement>('#label-body .label-cands');
  if (!it || !box || !panel.choice) return;
  const choice = panel.choice;
  box.innerHTML = '';
  it.candidates.forEach((c, i) => {
    const on = choice.checked.has(c.node_id);
    const row = el('div', `cand${on ? ' on' : ''}${c.extra ? ' extra' : ''}`);
    row.dataset['id'] = c.node_id;
    const line = el('label', 'cand-line');
    line.append(el('span', 'key', keyLabel(i)));
    const check = el('input') as HTMLInputElement;
    check.type = 'checkbox';
    check.checked = on;
    check.addEventListener('change', () => change(toggle(choice, c.node_id)));
    line.append(check);
    line.append(el('span', 'cand-name', candidateName(c)));
    if (c.edge) {
      const tag = el('span', `ev ev-${c.edge.evidence}`, c.edge.evidence);
      tag.title = c.edge.detail ? `${c.edge.evidence}: ${c.edge.detail}` : c.edge.evidence;
      line.append(tag);
      if (c.edge.detail) line.append(el('span', 'cand-detail', c.edge.detail));
    }
    if (c.extra) line.append(el('span', 'cand-detail', 'not a methods subsection now'));
    row.append(line);
    if (c.paragraphs.length) {
      const unfolded = panel.expanded.has(c.node_id);
      const marked = choice.paragraph.get(c.node_id);
      const twist = el('button', 'ghost small twist', `${unfolded ? '▾' : '▸'} ${c.paragraphs.length} ¶${marked ? ' · one marked' : ''}`);
      twist.title = unfolded ? 'Fold the paragraphs' : 'List the paragraphs, to mark the one the finding rests on (optional)';
      twist.addEventListener('click', (e) => {
        e.preventDefault();
        if (panel.expanded.has(c.node_id)) panel.expanded.delete(c.node_id);
        else panel.expanded.add(c.node_id);
        renderCandidates();
      });
      line.append(twist);
      if (unfolded) {
        const paras = el('div', 'cand-paras');
        for (const p of c.paragraphs) {
          const para = el('div', `para${marked === p.node_id ? ' on' : ''}`, p.text.length >= 200 ? `${p.text}…` : p.text);
          para.dataset['id'] = p.node_id;
          para.title = marked === p.node_id ? 'The paragraph this finding rests on: click to unmark' : 'Mark this as the paragraph the finding rests on';
          para.addEventListener('click', () => change(markParagraph(choice, c.node_id, p.node_id)));
          paras.append(para);
        }
        row.append(paras);
      }
    }
    box.append(row);
  });
  const none = el('div', `cand none${choice.none ? ' on' : ''}`);
  const noneLine = el('label', 'cand-line');
  noneLine.append(el('span', 'key', 'N'));
  const noneCheck = el('input') as HTMLInputElement;
  noneCheck.type = 'checkbox';
  noneCheck.checked = choice.none;
  noneCheck.addEventListener('change', () => change(toggleNone(choice)));
  noneLine.append(noneCheck, el('span', 'cand-name', 'No method in this paper'));
  none.append(noneLine);
  box.append(none);
  const nothing = !choice.none && !choice.checked.size;
  $('label-hint').textContent = nothing ? 'Nothing checked: saved as “the method is not among these”.' : '';
}

function change(next: Choice): void {
  panel.choice = next;
  renderCandidates();
}

async function save(): Promise<void> {
  const it = item();
  if (!it || !panel.choice || panel.saving || !panel.lib) return;
  panel.saving = true;
  $('label-error').textContent = '';
  try {
    await request('label', { lib: panel.lib, finding: it.finding.node_id, labels: labelsOf(panel.choice, it) });
    log('stage', `labelled: ${panel.choice.none ? 'no method in this paper' : `${panel.choice.checked.size} method${panel.choice.checked.size === 1 ? '' : 's'}`}`, it.paper);
  } catch (e) {
    $('label-error').textContent = `Not saved: ${(e as Error).message}`;
    return;
  } finally {
    panel.saving = false;
  }
  void refreshTruth();
  if (panel.single) {
    close();
    return;
  }
  await next();
}

async function next(): Promise<void> {
  panel.at += 1;
  if ((panel.stale || panel.at >= panel.items.length) && panel.lib) {
    panel.stale = false;
    await draw(panel.lib); // what is left once these are labelled, the model's findings first
  }
  show();
}

async function refreshTruth(): Promise<void> {
  if (!panel.lib) return;
  try {
    const t = await request<Truth>('truth', { lib: panel.lib });
    $('label-truth').textContent = truthLine(t);
    $('label-model-truth').textContent = modelLine(t);
  } catch (e) {
    $('label-truth').textContent = `The measure is not available: ${(e as Error).message}`;
  }
}

function onKey(e: KeyboardEvent): void {
  if (!open() || e.ctrlKey || e.metaKey || e.altKey) return;
  const target = e.target as HTMLElement | null;
  if (e.key === 'Escape') {
    e.preventDefault();
    close();
    return;
  }
  if (e.key === 'Enter') {
    if (target?.tagName === 'BUTTON' && $('label-panel').contains(target)) return; // a button of the panel that has the focus answers its own Enter
    e.preventDefault();
    void save();
    return;
  }
  const it = item();
  if (!it || !panel.choice) return;
  const i = keyIndex(e);
  if (i !== null) {
    const c = it.candidates[i];
    if (c) {
      e.preventDefault();
      change(toggle(panel.choice, c.node_id));
    }
    return;
  }
  if (e.key === 'n' || e.key === 'N') {
    e.preventDefault();
    change(toggleNone(panel.choice));
  } else if ((e.key === 's' || e.key === 'S') && !panel.single) {
    e.preventDefault();
    void next();
  }
}

/** The local model labels the findings the queue would offer (the worker's `model_label`, queued behind any reading). */
async function modelLabel(): Promise<void> {
  const lib = panel.lib ?? ctx.lib;
  if (!lib) return;
  $('label-error').textContent = '';
  $<HTMLButtonElement>('label-model').disabled = true;
  try {
    activity.show('The local model is labelling findings');
    await request('model_label', { lib, n: 100 });
    log('stage', 'the local model is labelling a hundred findings; the queue offers them first once it is done');
  } catch (e) {
    activity.hide();
    $<HTMLButtonElement>('label-model').disabled = false;
    $('label-error').textContent = `The model did not start: ${(e as Error).message}`;
  }
}

export function initLabels(): void {
  $('label-links').addEventListener('click', () => void openLabelling().catch((e) => log('error', `label links: ${(e as Error).message}`)));
  $('label-model').addEventListener('click', () => void modelLabel());
  onWorkerEvent((ev) => {
    if (ev['op'] !== 'model_label') return;
    if (ev['event'] === 'done') {
      $<HTMLButtonElement>('label-model').disabled = false;
      log('stage', `${ev['model']} labelled ${ev['labelled'] ?? 0} findings${ev['already'] ? `, ${ev['already']} it had already` : ''}${ev['unreadable'] ? `, ${ev['unreadable']} answers it could not read` : ''}`);
      if (ev['lib'] === panel.lib) {
        if (open() && !panel.single) panel.stale = true;
        else panel.items = [];
      }
      void refreshTruth();
    } else if (ev['event'] === 'error') {
      $<HTMLButtonElement>('label-model').disabled = false;
      $('label-error').textContent = `The model stopped: ${ev['message']}`;
    }
  });
  $('label-close').addEventListener('click', close);
  $('label-skip').addEventListener('click', () => void next());
  $('label-save').addEventListener('click', () => void save());
  $('label-panel').addEventListener('click', (e) => {
    if (e.target === $('label-panel')) close(); // a click on the dimmed window around the card
  });
  document.addEventListener('keydown', onKey);
}
