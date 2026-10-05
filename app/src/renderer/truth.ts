/**
 * The labelling editor's logic, kept apart from the DOM so it can be tested: the shapes the
 * worker's `label_queue` and `truth` ops return (parser/litrag_parser/truth.py), what the editor
 * starts from for a finding, how a key or a click changes it, and the labels it saves.
 */

export interface LabelParagraph {
  node_id: string;
  text: string;
}

/** a method the finding may have been measured by: a methods subsection, or a methods paragraph when the section has none */
export interface LabelCandidate {
  node_id: string;
  type: string;
  heading: string | null;
  text: string;
  paragraphs: LabelParagraph[];
  /** the linker's edge to it, when there is one */
  edge: { evidence: string; detail: string | null } | null;
  /** an edge's target that is no longer one of the paper's candidates */
  extra?: boolean;
}

export type Verdict = 'yes' | 'no' | 'none';

export interface SavedLabel {
  method: string;
  verdict: Verdict;
  paragraph: string | null;
  by?: string | null;
  at?: string;
}

export interface LabelItem {
  paper: string;
  title: string;
  doi: string | null;
  /** the DOI's registrant prefix: the publisher, near enough */
  prefix: string;
  /** what linked the finding: pointer · terms · caption · similarity · unlinked */
  evidence: string;
  finding: { node_id: string; text: string; ancestry: string[]; page: number | null; role: string };
  candidates: LabelCandidate[];
  edges: { dst: string; evidence: string; detail: string | null }[];
  labels: SavedLabel[];
}

interface Kind {
  edges: number;
  right: number;
  precision: number | null;
  unjudged: number;
}

/** the linker against the labels (the `truth` op) */
export interface Truth {
  labels: number;
  findings: number;
  papers: number;
  precision: Record<'pointer' | 'terms' | 'caption' | 'similarity' | 'all', Kind>;
  recall: { methods: number; reached: number; recall: number | null };
  misses: { count: number };
  false_links: { count: number };
  astray: number;
  outside: number;
  none: number;
  unanchored: { findings: number; methods: number; paragraphs: number };
  paragraph: { named: number; right: number; accuracy: number | null; chooser: string; first_paragraph?: number | null };
  /** the local model's labels against a person's, when it has labelled any */
  model?: ModelReport | null;
}

/** the local model's labels (parser/litrag_parser/labeller.py) against a person's: the audit */
export interface ModelReport {
  /** findings the model labelled */
  labelled: number;
  models: Record<string, number>;
  /** of those, the ones a person has labelled too */
  audited: number;
  agree: number;
  agreement: number | null;
  needed: number;
  gate: number;
  /** enough audited, and enough agreeing: its labels count for the findings no person labelled */
  stands: boolean;
  /** the linker measured with a person's labels and the model's for the rest, once it stands */
  measure: Truth | null;
}

/** The rule beside the question, for a person as for the model (labeller.py's RULE says the same). */
export const LABEL_RULE =
  'Tick the procedures that produced what the finding reports — several if it rests on several. Leave the preparation of the material and the statistics out unless the finding reports them.';

/** What the editor holds for one finding: the methods checked, the paragraph marked in each, or "no method in this paper". */
export interface Choice {
  checked: Set<string>;
  paragraph: Map<string, string>;
  none: boolean;
}

/** Where the editor starts: the finding's saved labels when it has any, else the linker's edges. */
export function initialChoice(item: LabelItem): Choice {
  const ids = new Set(item.candidates.map((c) => c.node_id));
  if (item.labels.length) {
    const yes = item.labels.filter((l) => l.verdict === 'yes' && ids.has(l.method));
    return {
      checked: new Set(yes.map((l) => l.method)),
      paragraph: new Map(yes.filter((l) => l.paragraph).map((l) => [l.method, l.paragraph!])),
      none: item.labels.some((l) => l.verdict === 'none'),
    };
  }
  return { checked: new Set(item.candidates.filter((c) => c.edge).map((c) => c.node_id)), paragraph: new Map(), none: false };
}

/** A method checked or unchecked; checking one undoes "no method in this paper", unchecking forgets its paragraph. */
export function toggle(choice: Choice, method: string): Choice {
  const checked = new Set(choice.checked);
  const paragraph = new Map(choice.paragraph);
  if (checked.has(method)) {
    checked.delete(method);
    paragraph.delete(method);
  } else checked.add(method);
  return { checked, paragraph, none: false };
}

/** "No method in this paper": it unchecks every method. */
export function toggleNone(choice: Choice): Choice {
  return choice.none ? { ...choice, none: false } : { checked: new Set(), paragraph: new Map(), none: true };
}

/** The paragraph a finding rests on, marked inside a method (which it checks); marking it again unmarks it. */
export function markParagraph(choice: Choice, method: string, paragraph: string): Choice {
  const checked = new Set(choice.checked).add(method);
  const marks = new Map(choice.paragraph);
  if (marks.get(method) === paragraph) marks.delete(method);
  else marks.set(method, paragraph);
  return { checked, paragraph: marks, none: false };
}

/** The labels a choice saves: `none` alone, or every candidate yes or no — a complete set, so an edge to any of them is judged. */
export function labelsOf(choice: Choice, item: LabelItem): { method: string; verdict: Verdict; paragraph?: string }[] {
  if (choice.none) return [{ method: '', verdict: 'none' }];
  return item.candidates.map((c) => {
    const yes = choice.checked.has(c.node_id);
    const p = yes ? choice.paragraph.get(c.node_id) : undefined;
    return p ? { method: c.node_id, verdict: 'yes', paragraph: p } : { method: c.node_id, verdict: yes ? 'yes' : 'no' };
  });
}

/** The candidate a key names: 1–9 the first nine, 0 the tenth, Shift with 1–9 the eleventh to nineteenth. */
export function keyIndex(e: { key: string; code?: string; shiftKey?: boolean }): number | null {
  const digit = /^Digit(\d)$/.exec(e.code ?? '')?.[1] ?? (/^\d$/.test(e.key) ? e.key : null);
  if (digit === null) return null;
  const d = Number(digit);
  if (e.shiftKey) return d === 0 ? null : 9 + d;
  return d === 0 ? 9 : d - 1;
}

/** The key that toggles candidate `i`, as `keyIndex` reads it. */
export function keyLabel(i: number): string {
  return i < 9 ? String(i + 1) : i === 9 ? '0' : i < 19 ? `⇧${i - 9}` : '';
}

/** A candidate's name: its heading, or a methods paragraph's first words. */
export function candidateName(c: LabelCandidate): string {
  if (c.heading) return c.heading;
  const t = c.text.replace(/\s+/g, ' ').trim();
  return t.length > 90 ? `${t.slice(0, 90)}…` : t || c.node_id;
}

const pct = (k: Kind): string => (k.precision === null ? '–' : `${k.precision.toFixed(2)} (${k.right}/${k.edges})`);

/** The running line under the editor: how many are labelled and how the linker fares so far, per evidence. */
export function truthLine(t: Truth): string {
  if (!t.findings) return 'Nothing labelled yet: the precision of each kind of evidence appears as findings are labelled.';
  const kinds = (['pointer', 'terms', 'caption', 'similarity'] as const).filter((k) => t.precision[k].edges).map((k) => `${k} ${pct(t.precision[k])}`);
  const parts = [
    `${t.findings} labelled`,
    `precision so far by evidence: ${kinds.length ? kinds.join(' · ') : 'no edge judged yet'}`,
    t.recall.recall === null ? '' : `recall ${t.recall.recall.toFixed(2)} (${t.recall.reached}/${t.recall.methods})`,
    t.misses.count ? `${t.misses.count} missed` : '',
    t.false_links.count ? `${t.false_links.count} false link${t.false_links.count === 1 ? '' : 's'}` : '',
  ];
  return parts.filter(Boolean).join(' · ');
}

/** The line under it about the local model: how far its labels are audited, how often a person agrees, and once they stand, the measure with them. Empty when it has labelled nothing. */
export function modelLine(t: Truth): string {
  const m = t.model;
  if (!m || !m.labelled) return '';
  const who = Object.keys(m.models).join(', ') || 'the model';
  const agrees = m.audited ? ` · you agree on ${m.agree} (${(m.agreement ?? 0).toFixed(2)})` : '';
  if (m.stands && m.measure) return `${who}’s labels stand (${m.agree} of ${m.audited} audited agree): with them, ${truthLine(m.measure)}`;
  return `${who} labelled ${m.labelled} · ${Math.min(m.audited, m.needed)} of ${m.needed} audited${agrees} — its labels count once ${m.needed} are audited at ${m.gate} agreement`;
}
