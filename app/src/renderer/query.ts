/**
 * The Query tab: a question, the passages that answer it, and each passage hydrated from the
 * tree it came from — where it sits (the headings above it), the paragraphs either side, and,
 * for a finding, the methods it was measured by and the figures it cites. A passage is a
 * paragraph node; its context is the rows around it, not a wider chunk.
 */

import { renderPlots, type Plot } from './charts.ts';
import { $, activity, ctx, el, escapeHtml, hooks, log, onProjectChange, onViewShown, onWorkerEvent, projectName, request, roleColor } from './shared.ts';

interface QNode {
  node_id: string;
  text: string;
  role: string;
  type?: string;
  heading?: string | null;
  ancestry?: string[];
  page?: number | null;
}

/** Where a method's procedure is written down when it says "as previously described [14]": the
 *  cited paper's own method when the project holds it, else the reference (lineage.py). */
interface QElsewhere {
  sentence: string;
  marker?: string;
  ref_no: number;
  ref: { text?: string; doi?: string | null; year?: string | null; first_author?: string | null; title?: string | null };
  paper: { key: string; title: string; year?: string | null } | null;
  method: { node_id: string; heading?: string | null; text: string; evidence: string } | null;
  candidate?: { cand_id: number; status: string } | null;
}

interface QMethod {
  node_id: string;
  heading?: string | null;
  ancestry?: string[];
  text: string;
  evidence?: string;
  detail?: string | null;
  score?: number | null;
  via?: 'hit' | 'figure' | 'section';
  /** the paragraph of the method the finding rests on, and the terms that chose it */
  paragraph?: string | null;
  matched?: string[];
  paragraphs?: number;
  described_in?: QElsewhere[];
}

interface QFindings {
  method: string;
  heading?: string | null;
  total: number;
  findings: { node_id: string; text: string; role: string; page?: number | null; evidence: string; detail?: string | null }[];
}

export interface QHit {
  rank: number;
  score: number;
  ranks?: Record<string, number>;
  hit: QNode;
  paper: { key: string; title: string; year?: string | null; journal?: string | null; doi?: string | null; type?: string | null };
  section?: { node_id: string; heading: string | null; role: string; canonical?: string | null } | null;
  before?: QNode[];
  after?: QNode[];
  methods?: QMethod[];
  /** statistics and materials: every finding's, shown apart so the measuring method comes first */
  general?: QMethod[];
  /** for a methods hit: what its method measured, the edges walked the other way */
  findings?: QFindings | null;
  described_in?: QElsewhere[];
  figures?: { node_id: string; text: string; caption?: string; label?: string; data?: Plot[] }[];
  cites?: { ref_no: number; first_author?: string | null; year?: string | null; title?: string | null; doi?: string | null; text?: string; work_paper?: string | null; work_status?: string | null; work_title?: string | null }[];
  also?: string[];
}

interface QAnswer {
  question: string;
  hits: QHit[];
  embedder?: { model?: string; down?: boolean; error?: string | null };
  counts?: Record<string, number>;
  seconds?: number;
  note?: string;
}

export function initQuery(): void {
  $<HTMLFormElement>('query-form').addEventListener('submit', (e) => {
    e.preventDefault();
    void ask();
  });
  $<HTMLTextAreaElement>('query-q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void ask();
    }
  });
  $('embed').addEventListener('click', () => void embed());
  onProjectChange(() => {
    $('query-results').innerHTML = '';
    $('query-meta').innerHTML = '';
    if (ctx.view === 'query') void loadStatus();
  });
  onViewShown((v) => {
    if (v === 'query') void loadStatus();
  });
  onWorkerEvent((ev) => {
    if (ev['event'] === 'done' && ev['op'] === 'embed') {
      activity.hide();
      log('stage', `Embedded ${ev['embedded'] ?? 0} passages${ev['error'] ? ` — ${ev['error']}` : ''}`);
      void loadStatus();
    }
  });
}

async function loadStatus(): Promise<void> {
  $('query-project').textContent = projectName();
  const lib = ctx.lib;
  if (!lib) return;
  try {
    const r = await request<{ units: number; embedded: number; model: string; down?: boolean; error?: string | null }>('retrieval', { lib });
    if (ctx.lib !== lib) return;
    const s = $('embed-status');
    s.textContent = `${r.embedded.toLocaleString()} of ${r.units.toLocaleString()} passages embedded · ${r.model}${r.down ? ' · the embedder is not answering' : ''}`;
    s.dataset['embedded'] = String(r.embedded);
    s.dataset['units'] = String(r.units);
    $<HTMLButtonElement>('embed').disabled = r.units > 0 && r.embedded >= r.units;
  } catch (e) {
    $('embed-status').textContent = `retrieval: ${(e as Error).message}`;
  }
}

async function embed(): Promise<void> {
  if (!ctx.lib) return;
  try {
    activity.show('Embedding passages');
    await request('embed', { lib: ctx.lib });
  } catch (e) {
    activity.hide();
    log('error', `embed: ${(e as Error).message}`);
  }
}

async function ask(): Promise<void> {
  if (!ctx.lib) {
    window.alert('Choose a project first.');
    return;
  }
  const question = $<HTMLTextAreaElement>('query-q').value.trim();
  if (!question) return;
  const k = Math.max(1, Math.min(30, Number($<HTMLInputElement>('query-k').value) || 8));
  const box = $('query-results');
  box.innerHTML = '';
  box.append(el('div', 'empty', 'Retrieving and hydrating…'));
  const t = performance.now();
  const lib = ctx.lib;
  try {
    const r = await request<QAnswer>('query', { lib, question, k });
    if (ctx.lib !== lib) return;
    renderAnswer(r, (performance.now() - t) / 1000);
  } catch (e) {
    box.innerHTML = '';
    box.append(el('div', 'empty', `The query failed: ${(e as Error).message}`));
  }
}

const STOP = new Set('a an and are as at be by for from has have in is it its of on or that the this to was were which with what how does do did at when why who whom whose between into than then'.split(' '));

function highlight(text: string, question: string): string {
  // the words are found in the raw text and each piece escaped on its own, so a query word can
  // never match inside an entity ("&amp;") or the <mark> this inserts
  const terms = [...new Set(question.toLowerCase().match(/[a-z0-9°]{3,}/g) ?? [])].filter((w) => !STOP.has(w));
  if (!terms.length) return escapeHtml(text);
  const re = new RegExp(`\\b(?:${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\w*`, 'gi');
  let out = '';
  let last = 0;
  for (const m of text.matchAll(re)) {
    out += escapeHtml(text.slice(last, m.index)) + `<mark>${escapeHtml(m[0])}</mark>`;
    last = m.index! + m[0].length;
  }
  return out + escapeHtml(text.slice(last));
}

function renderAnswer(r: QAnswer, seconds: number): void {
  const meta = $('query-meta');
  meta.innerHTML = '';
  meta.append(el('span', undefined, `${r.hits.length} passage${r.hits.length === 1 ? '' : 's'} in ${seconds.toFixed(2)} s`));
  if (r.embedder?.model) meta.append(el('span', undefined, `meaning by ${r.embedder.model}`));
  if (r.embedder?.down) meta.append(el('span', 'conf low', 'the embedder is not answering: words only'));
  if (r.note) meta.append(el('span', undefined, r.note));
  const box = $('query-results');
  box.innerHTML = '';
  if (!r.hits.length) box.append(el('div', 'empty', 'Nothing in this project answers that.'));
  for (const h of r.hits) box.append(hitCard(h, r.question));
}

function hitCard(h: QHit, question: string): HTMLElement {
  const card = el('div', 'qhit');
  card.dataset['node'] = h.hit.node_id;
  const head = el('div', 'qhit-head');
  head.append(el('span', 'rank', `${h.rank}`), el('span', 'ptitle', h.paper.title));
  head.append(el('span', 'pmeta', [h.paper.journal ?? '', h.paper.year ?? '', h.paper.doi ? `doi:${h.paper.doi}` : ''].filter(Boolean).join(' · ')));
  const ranks = el('span', 'ranks', Object.entries(h.ranks ?? {}).map(([k, v]) => `${k} #${v}`).join(' · '));
  ranks.title = 'where the passage ranked by each list before they were fused';
  head.append(ranks);
  card.append(head);

  const body = el('div', 'qhit-body');
  const main = el('div', 'qhit-main');
  const crumb = el('div', 'crumb');
  const trail = [...(h.hit.ancestry ?? [])];
  crumb.innerHTML = trail.map((s) => `<b>${escapeHtml(s)}</b>`).join(' › ') + ` <span style="color:${roleColor(h.hit.role)}">· ${escapeHtml(h.hit.role)}</span>${h.hit.page ? ` · p.${h.hit.page}` : ''}`;
  main.append(crumb);
  for (const b of h.before ?? []) main.append(el('div', 'ctx before', b.text));
  const p = el('div', 'hitp');
  p.innerHTML = highlight(h.hit.text, question);
  main.append(p);
  for (const a of h.after ?? []) main.append(el('div', 'ctx after', a.text));
  if (h.also?.length) main.append(el('div', 'muted', `also matched: ${h.also.length} neighbouring passage${h.also.length === 1 ? '' : 's'}, shown in the context`));
  body.append(main);

  const side = el('div', 'qhit-side');
  if (h.methods?.length) {
    side.append(el('div', 'side-h', `Measured by (${h.methods.length})`));
    for (const m of h.methods) side.append(methodItem(h.paper.key, m));
  } else if (['results', 'results-discussion', 'discussion'].includes(h.hit.role)) {
    side.append(el('div', 'side-h', 'Measured by'));
    side.append(el('div', 'muted', 'No method is linked to this passage in the tree.'));
  }
  if (h.general?.length) {
    // statistics and materials belong to every finding of the paper: named, and opened on a click
    side.append(el('div', 'side-h', 'Also used'));
    for (const g of h.general) {
      const item = el('div', 'side-item general');
      item.append(el('div', 'h', g.heading ?? 'Methods'));
      const text = el('div', 'body', g.text.length > 700 ? `${g.text.slice(0, 700)}…` : g.text);
      text.hidden = true;
      item.append(text);
      item.style.cursor = 'pointer';
      item.title = 'Show it';
      item.addEventListener('click', () => {
        text.hidden = !text.hidden;
      });
      side.append(item);
    }
  }
  if (h.findings?.findings.length) {
    side.append(el('div', 'side-h', `Findings measured here (${h.findings.total})`));
    for (const f of h.findings.findings) {
      const item = el('div', 'side-item finding');
      item.append(el('div', undefined, f.text));
      item.append(el('div', 'ev', [f.evidence, f.detail].filter(Boolean).join(': ')));
      item.style.cursor = 'pointer';
      item.addEventListener('click', () => hooks.openPaper(h.paper.key, f.node_id));
      side.append(item);
    }
    if (h.findings.total > h.findings.findings.length) side.append(el('div', 'muted', `and ${h.findings.total - h.findings.findings.length} more, in the tree`));
  }
  if (h.described_in?.length) {
    side.append(el('div', 'side-h', 'Described in'));
    for (const d of h.described_in) side.append(elsewhereItem(d));
  }
  if (h.figures?.length) {
    side.append(el('div', 'side-h', `Figures cited (${h.figures.length})`));
    for (const f of h.figures) {
      const item = el('div', 'side-item', f.caption ?? f.text);
      item.addEventListener('click', () => hooks.openPaper(h.paper.key, f.node_id));
      item.style.cursor = 'pointer';
      if (f.data?.length) renderPlots(item, f.data, { compact: true }); // the numbers read from it, the panel the passage names first
      side.append(item);
    }
  }
  if (h.cites?.length) {
    side.append(el('div', 'side-h', `Cites (${h.cites.length})`));
    for (const c of h.cites.slice(0, 6)) {
      const item = el('div', 'side-item', `[${c.ref_no}] ${[c.first_author, c.year].filter(Boolean).join(' ')} — ${c.title ?? c.work_title ?? c.text ?? ''}${c.doi ? ` · doi:${c.doi}` : ''}`);
      if (c.work_paper) {
        // the cited paper is in the library: the passage leads to it
        item.classList.add('go');
        item.append(el('span', 'held', ' → in the library'));
        item.title = 'Open the cited paper';
        item.addEventListener('click', () => hooks.openPaper(c.work_paper!));
      } else if (c.work_status) {
        item.append(el('span', 'muted', ` → ${c.work_status}`));
      }
      side.append(item);
    }
  }
  if (h.section) {
    side.append(el('div', 'side-h', 'Section'));
    side.append(el('div', 'side-item', `${h.section.heading ?? '(untitled)'} · ${h.section.role}${h.section.canonical ? ` · ${h.section.canonical}` : ''}`));
  }
  body.append(side);
  card.append(body);

  const foot = el('div', 'qhit-foot');
  const open = el('button', 'ghost small', 'Open in the tree');
  open.addEventListener('click', () => hooks.openPaper(h.paper.key, h.hit.node_id));
  foot.append(open);
  card.append(foot);
  return card;
}

const VIA: Record<string, string> = {
  figure: 'from the paragraphs citing this figure',
  section: "from the other findings of this section: the section's methods, not this paragraph's",
};

/** One method a passage was measured by: its heading, the paragraph of it the passage rests on
 *  (with the terms that chose it) or its opening when no paragraph stands out, the evidence of
 *  the edge, and where the procedure is written down when it is "as previously described". */
function methodItem(paperKey: string, m: QMethod): HTMLElement {
  const item = el('div', 'side-item methods');
  item.dataset['node'] = m.node_id;
  item.append(el('div', 'h', m.heading ?? (m.ancestry ?? []).slice(-1)[0] ?? 'Methods'));
  if (m.paragraph && (m.paragraphs ?? 1) > 1) {
    const why = el('div', 'ev', `the paragraph of ${m.paragraphs} on ${m.matched?.length ? m.matched.slice(0, 3).join(', ') : 'its words'}`);
    item.append(why);
  }
  item.append(el('div', undefined, m.text.length > 700 ? `${m.text.slice(0, 700)}…` : m.text));
  item.append(el('div', 'ev', [m.evidence, m.detail].filter(Boolean).join(': ')));
  if (m.via && VIA[m.via]) item.append(el('div', 'ev', VIA[m.via]!));
  item.style.cursor = 'pointer';
  item.addEventListener('click', () => hooks.openPaper(paperKey, m.paragraph ?? m.node_id));
  for (const d of m.described_in ?? []) item.append(elsewhereItem(d));
  return item;
}

/** "As previously described [14]": the cited paper's own method when the project holds the
 *  paper, else the reference itself, so it can be fetched. */
function elsewhereItem(d: QElsewhere): HTMLElement {
  const box = el('div', 'elsewhere');
  const ref = [d.ref.first_author, d.ref.year].filter(Boolean).join(' ') || `ref ${d.ref_no}`;
  if (d.paper) {
    box.append(el('div', 'ev', `described in ${d.marker ?? `[${d.ref_no}]`} — ${d.paper.title}${d.paper.year ? ` (${d.paper.year})` : ''}`));
    if (d.method) {
      box.append(el('div', 'h', d.method.heading ?? 'Methods'));
      box.append(el('div', undefined, d.method.text.length > 500 ? `${d.method.text.slice(0, 500)}…` : d.method.text));
      box.style.cursor = 'pointer';
      box.addEventListener('click', (e) => {
        e.stopPropagation();
        hooks.openPaper(d.paper!.key, d.method!.node_id);
      });
    }
  } else {
    const where = d.candidate ? ` · a candidate of this project (${d.candidate.status})` : ' · not in this project';
    box.append(el('div', 'ev', `described in ${d.marker ?? `[${d.ref_no}]`} — ${ref}${d.ref.title ? `: ${d.ref.title}` : ''}${d.ref.doi ? ` · doi:${d.ref.doi}` : ''}${where}`));
  }
  box.title = d.sentence;
  return box;
}
