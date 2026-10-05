/**
 * The numbers read from a figure (parser/litrag_parser/figures.py), as the window shows them:
 * each plot a small table — categories or x down the side, series across, each value with its
 * error bar — its axis and unit above it, a CSV of it a click away. The layout is pure so it can
 * be tested; `renderPlots` draws it.
 */

import { el } from './shared.ts';

export interface ChartValue {
  category: string | null;
  x: number | null;
  y: number;
  err_lo: number | null;
  err_hi: number | null;
}

export interface ChartSeries {
  name: string | null;
  colour: string | null;
  values: ChartValue[];
}

export interface Plot {
  plot?: number;
  panel: string | null;
  kind: 'bar' | 'point' | null;
  status: 'read' | 'unread';
  reason: string | null;
  source?: string;
  y: { label: string | null; unit: string | null; scale: string | null };
  x: { label: string | null; unit: string | null; scale: string | null };
  categories: (string | null)[];
  series: ChartSeries[];
  /** set by query hydration: the passage names this panel */
  cited?: boolean;
}

/** A number as a figure would print it: three significant digits, no trailing noise. */
export function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '';
  if (v === 0) return '0';
  const digits = Math.max(0, 2 - Math.floor(Math.log10(Math.abs(v))));
  return Number(v.toFixed(Math.min(digits, 6))).toString();
}

/** A value with its error bar: `12.5 ± 2.1` when both whiskers show and agree, else each one that shows. */
export function withError(v: ChartValue): string {
  const y = fmt(v.y);
  const { err_lo: lo, err_hi: hi } = v;
  if (hi !== null && lo !== null && Math.abs(hi - lo) <= 0.15 * Math.max(hi, lo)) return `${y} ± ${fmt((hi + lo) / 2)}`;
  if (hi !== null && lo !== null) return `${y} +${fmt(hi)} −${fmt(lo)}`;
  if (hi !== null) return `${y} ± ${fmt(hi)}`;
  if (lo !== null) return `${y} −${fmt(lo)}`;
  return y;
}

export function seriesName(s: ChartSeries, i: number, all: ChartSeries[]): string {
  return s.name ?? (all.length === 1 ? 'value' : `series ${i + 1}`);
}

/** The plot as a table: a row per category (bars) or per x (points), a column per series. */
export function plotTable(p: Plot): { head: string[]; rows: string[][] } {
  const names = p.series.map((s, i) => seriesName(s, i, p.series));
  if (p.kind === 'bar') {
    const cats: string[] = [];
    for (const s of p.series) for (const v of s.values) if (!cats.includes(v.category ?? '')) cats.push(v.category ?? '');
    const rows = cats.map((c) => [c || '—', ...p.series.map((s) => {
      const v = s.values.find((x) => (x.category ?? '') === c);
      return v ? withError(v) : '';
    })]);
    return { head: ['', ...names], rows };
  }
  const xs = [...new Set(p.series.flatMap((s) => s.values.map((v) => v.x)).filter((x): x is number => x !== null))].sort((a, b) => a - b);
  const near = (a: number, b: number) => Math.abs(a - b) <= 0.02 * Math.max(1, Math.abs(xs[xs.length - 1] ?? 1));
  const rows: string[][] = [];
  for (const x of xs) {
    if (rows.length && near(Number(rows[rows.length - 1]![0]), x)) continue; // the same x read twice, a hair apart
    rows.push([fmt(x), ...p.series.map((s) => {
      const v = s.values.find((q) => q.x !== null && near(q.x, x));
      return v ? withError(v) : '';
    })]);
  }
  return { head: [p.x.label || 'x', ...names], rows };
}

/** One plot as CSV — the same as figures.csv_of. */
export function csvOf(p: Plot): string {
  const cell = (v: unknown) => {
    if (v === null || v === undefined) return '';
    const t = String(v);
    return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  const lines = ['series,category,x,y,err_lo,err_hi'];
  p.series.forEach((s, i) => {
    for (const v of s.values) lines.push([s.name ?? `series ${i + 1}`, v.category, v.x, v.y, v.err_lo, v.err_hi].map(cell).join(','));
  });
  return `${lines.join('\n')}\n`;
}

export function plotTitle(p: Plot): string {
  const parts = [p.panel ? `(${p.panel})` : '', p.y.label || 'y', p.y.scale === 'log' ? '· log scale' : ''];
  return parts.filter(Boolean).join(' ');
}

/** The plots drawn into `into`: a table each, or why it was not read. */
export function renderPlots(into: HTMLElement, plots: Plot[], opts: { compact?: boolean } = {}): void {
  for (const p of plots) {
    const box = el('div', `chart${p.cited ? ' cited' : ''}`);
    const head = el('div', 'chart-head');
    head.append(el('span', 'chart-title', plotTitle(p)));
    if (p.status !== 'read') {
      head.append(el('span', 'muted', ` — not read: ${p.reason ?? 'no reason given'}`));
      box.append(head);
      into.append(box);
      continue;
    }
    if (p.cited) head.append(el('span', 'pill', 'cited'));
    const copy = el('button', 'ghost small', 'CSV');
    copy.title = 'Copy this plot’s values as CSV';
    copy.addEventListener('click', (ev) => {
      ev.stopPropagation();
      void navigator.clipboard?.writeText(csvOf(p)).then(() => {
        copy.textContent = 'copied';
        setTimeout(() => (copy.textContent = 'CSV'), 1200);
      });
    });
    head.append(copy);
    box.append(head);
    const t = plotTable(p);
    const table = el('table', 'chart-table');
    const tr = el('tr');
    for (const h of t.head) tr.append(el('th', undefined, h));
    table.append(tr);
    for (const row of opts.compact ? t.rows.slice(0, 8) : t.rows) {
      const r = el('tr');
      row.forEach((c, i) => r.append(el(i ? 'td' : 'th', undefined, c)));
      table.append(r);
    }
    box.append(table);
    if (opts.compact && t.rows.length > 8) box.append(el('div', 'muted', `… ${t.rows.length - 8} more rows`));
    into.append(box);
  }
}
