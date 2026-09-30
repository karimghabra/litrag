/**
 * The paper list's order and filters, apart from the DOM so they can be tested: sort by the
 * order papers were added, by format (PDF, XML), by type, title, year, or by how far the
 * reading can be trusted; show only one format, one type of paper, or one band of confidence.
 * A paper still being read has no type and no score yet and is never filtered out by them —
 * it would vanish from the list while the person watches it being read.
 */

export type SortKey = 'added' | 'format' | 'type' | 'title' | 'year' | 'confidence';

export const SORT_KEYS: { key: SortKey; label: string }[] = [
  { key: 'added', label: 'as added' },
  { key: 'format', label: 'format' },
  { key: 'type', label: 'type' },
  { key: 'title', label: 'title' },
  { key: 'year', label: 'year, newest first' },
  { key: 'confidence', label: 'confidence, lowest first' },
];

/** what the list needs to know of a paper */
export interface Listed {
  key: string;
  title?: string | null;
  file?: string | null;
  format?: string | null;
  status?: string;
  type?: string | null;
  year?: string | null;
  /** how far the reading can be trusted, in (0, 1] (the worker's confidence.py); null until it is read */
  confidence?: number | null;
}

export interface Filters {
  format: string | null;
  type: string | null;
  band?: string | null;
}

/**
 * The bands the score was measured in, on papers held as both PDF and XML: at 0.9 or more,
 * four readings in five matched the XML well; under 0.5, five in six were seriously off.
 */
export const BANDS: { id: string; label: string; from: number }[] = [
  { id: 'high', label: '≥ 0.9', from: 0.9 },
  { id: 'middle', label: '0.5 – 0.9', from: 0.5 },
  { id: 'low', label: '< 0.5', from: 0 },
];

export function bandOf(p: Listed): string | null {
  if (p.confidence === null || p.confidence === undefined) return null;
  return BANDS.find((b) => (p.confidence as number) >= b.from)?.id ?? 'low';
}

/** the order types are listed in: the kinds a library is mostly made of first, `other` last */
const TYPE_ORDER = ['research', 'review', 'case-report', 'protocol', 'data', 'letter', 'editorial', 'correction', 'other'];

/** "pdf" or "xml": the store says `jats` for the XML a publisher deposits, a person says XML */
export function formatOf(p: Listed): string {
  const f = (p.format ?? '').toLowerCase();
  if (f === 'jats' || f === 'xml') return 'xml';
  if (f === 'pdf') return 'pdf';
  const ext = (p.file ?? '').toLowerCase().split('.').pop() ?? '';
  return ext === 'xml' ? 'xml' : ext === 'pdf' ? 'pdf' : 'unknown';
}

/** the paper's type, or `unread` while it has none (queued, being read, failed) */
export function typeOf(p: Listed): string {
  return p.type ? p.type : 'unread';
}

function rank(order: string[], value: string): number {
  const i = order.indexOf(value);
  return i === -1 ? order.length : i;
}

/** A new array in the chosen order; ties keep the order the papers came in (added order). */
export function sortPapers<T extends Listed>(papers: T[], key: SortKey): T[] {
  const at = new Map(papers.map((p, i) => [p, i] as const));
  const tie = (a: T, b: T) => (at.get(a) ?? 0) - (at.get(b) ?? 0);
  const out = [...papers];
  switch (key) {
    case 'format':
      return out.sort((a, b) => formatOf(a).localeCompare(formatOf(b)) || tie(a, b));
    case 'type':
      return out.sort((a, b) => rank([...TYPE_ORDER, 'unread'], typeOf(a)) - rank([...TYPE_ORDER, 'unread'], typeOf(b)) || typeOf(a).localeCompare(typeOf(b)) || tie(a, b));
    case 'title':
      return out.sort((a, b) => (a.title || a.file || a.key).localeCompare(b.title || b.file || b.key, undefined, { sensitivity: 'base' }) || tie(a, b));
    case 'year':
      return out.sort((a, b) => (Number(b.year) || 0) - (Number(a.year) || 0) || tie(a, b));
    case 'confidence': // the readings to look at first; a paper with no score yet goes last
      return out.sort((a, b) => (a.confidence ?? 2) - (b.confidence ?? 2) || tie(a, b));
    default:
      return out;
  }
}

/** The papers the filters let through. A paper with no type yet passes the type filter. */
export function filterPapers<T extends Listed>(papers: T[], filters: Filters): T[] {
  return papers.filter(
    (p) => (!filters.format || formatOf(p) === filters.format) && (!filters.type || !p.type || p.type === filters.type) && (!filters.band || bandOf(p) === null || bandOf(p) === filters.band),
  );
}

/** `[value, count]` for the chips: formats alphabetically, types in `TYPE_ORDER`; papers with no type are left out. */
export function countBy<T extends Listed>(papers: T[], of: 'format' | 'type' | 'band'): [string, number][] {
  const counts = new Map<string, number>();
  for (const p of papers) {
    if (of === 'type' && !p.type) continue;
    const v = of === 'format' ? formatOf(p) : of === 'band' ? bandOf(p) : typeOf(p);
    if (v === null) continue;
    counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  const entries = [...counts.entries()];
  if (of === 'band') return entries.sort((a, b) => BANDS.findIndex((x) => x.id === a[0]) - BANDS.findIndex((x) => x.id === b[0]));
  return of === 'format' ? entries.sort((a, b) => a[0].localeCompare(b[0])) : entries.sort((a, b) => rank(TYPE_ORDER, a[0]) - rank(TYPE_ORDER, b[0]) || a[0].localeCompare(b[0]));
}

/** A filter naming a value no paper has any more (another library was opened) is dropped. */
export function validFilters<T extends Listed>(papers: T[], filters: Filters): Filters {
  const formats = new Set(papers.map(formatOf));
  const types = new Set(papers.map((p) => p.type).filter(Boolean));
  const bands = new Set(papers.map(bandOf));
  return { format: filters.format && formats.has(filters.format) ? filters.format : null, type: filters.type && types.has(filters.type) ? filters.type : null, band: filters.band && bands.has(filters.band) ? filters.band : null };
}
