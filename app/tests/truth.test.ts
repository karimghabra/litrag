import { describe, expect, it } from 'vitest';
import { candidateName, initialChoice, keyIndex, keyLabel, labelsOf, markParagraph, toggle, toggleNone, truthLine, type LabelItem, type Truth } from '../src/renderer/truth';

const cand = (id: string, heading: string | null, edge: string | null = null, paragraphs: string[] = []) => ({
  node_id: id, type: heading ? 'section' : 'paragraph', heading, text: heading ? '' : 'Compressive modulus was measured on an Instron at 1 mm/min.',
  paragraphs: paragraphs.map((p) => ({ node_id: p, text: `paragraph ${p}` })), edge: edge ? { evidence: edge, detail: 'compressive modulus' } : null,
});

const item = (labels: LabelItem['labels'] = []): LabelItem => ({
  paper: 'doi:1', title: 'A paper', doi: '10.1/x', prefix: '10.1', evidence: 'terms',
  finding: { node_id: 'f', text: 'The modulus rose.', ancestry: ['3 Results'], page: 4, role: 'results' },
  candidates: [cand('m1', '2.1 Fabrication', null, ['p1']), cand('m2', '2.2 Mechanical testing', 'terms', ['p2', 'p3']), cand('m3', '2.3 Swelling', 'terms')],
  edges: [{ dst: 'm2', evidence: 'terms', detail: null }, { dst: 'm3', evidence: 'terms', detail: null }],
  labels,
});

describe('the labelling editor', () => {
  it('starts from the linker’s edges, or from the labels the finding already has', () => {
    const fresh = initialChoice(item());
    expect([...fresh.checked]).toEqual(['m2', 'm3']);
    expect(fresh.none).toBe(false);
    const saved = initialChoice(item([{ method: 'm1', verdict: 'yes', paragraph: 'p1' }, { method: 'm2', verdict: 'no', paragraph: null }, { method: 'gone', verdict: 'yes', paragraph: null }]));
    expect([...saved.checked]).toEqual(['m1']); // a label for a method that is no longer a candidate checks nothing
    expect(saved.paragraph.get('m1')).toBe('p1');
    expect(initialChoice(item([{ method: '', verdict: 'none', paragraph: null }]))).toEqual({ checked: new Set(), paragraph: new Map(), none: true });
  });

  it('saves every candidate yes or no, the marked paragraph on a yes, or none alone', () => {
    let c = initialChoice(item());
    c = toggle(c, 'm3');
    c = markParagraph(c, 'm2', 'p3');
    expect(labelsOf(c, item())).toEqual([
      { method: 'm1', verdict: 'no' },
      { method: 'm2', verdict: 'yes', paragraph: 'p3' },
      { method: 'm3', verdict: 'no' },
    ]);
    c = markParagraph(c, 'm1', 'p1'); // marking a paragraph checks its method
    expect(labelsOf(c, item())[0]).toEqual({ method: 'm1', verdict: 'yes', paragraph: 'p1' });
    c = markParagraph(c, 'm1', 'p1'); // and marking it again unmarks the paragraph, the method stays
    expect(labelsOf(c, item())[0]).toEqual({ method: 'm1', verdict: 'yes' });
    c = toggle(c, 'm2'); // unchecking forgets the paragraph
    expect(c.paragraph.has('m2')).toBe(false);
    const none = toggleNone(c);
    expect(labelsOf(none, item())).toEqual([{ method: '', verdict: 'none' }]);
    expect(none.checked.size).toBe(0);
    expect(toggle(none, 'm1').none).toBe(false); // checking a method undoes "no method in this paper"
    expect(toggleNone(none).none).toBe(false);
  });

  it('reads the number keys: 1–9, 0 for the tenth, Shift for the eleventh on', () => {
    expect(keyIndex({ key: '1', code: 'Digit1' })).toBe(0);
    expect(keyIndex({ key: '9', code: 'Digit9' })).toBe(8);
    expect(keyIndex({ key: '0', code: 'Digit0' })).toBe(9);
    expect(keyIndex({ key: '!', code: 'Digit1', shiftKey: true })).toBe(10);
    expect(keyIndex({ key: '3', code: 'Numpad3' })).toBe(2);
    expect(keyIndex({ key: 'n', code: 'KeyN' })).toBeNull();
    expect([0, 8, 9, 10, 14].map(keyLabel)).toEqual(['1', '9', '0', '⇧1', '⇧5']);
    for (const i of [0, 5, 9, 10, 18]) {
      const label = keyLabel(i);
      const shift = label.startsWith('⇧');
      const d = label.replace('⇧', '');
      expect(keyIndex({ key: d, code: `Digit${d}`, shiftKey: shift })).toBe(i);
    }
  });

  it('names a candidate by its heading, or a methods paragraph by its first words', () => {
    expect(candidateName(cand('m', '2.2 Mechanical testing'))).toBe('2.2 Mechanical testing');
    expect(candidateName(cand('p', null))).toBe('Compressive modulus was measured on an Instron at 1 mm/min.');
  });

  it('says how the linker fares so far, per evidence', () => {
    const kind = (right: number, edges: number) => ({ edges, right, precision: edges ? Math.round((1000 * right) / edges) / 1000 : null, unjudged: 0 });
    const t: Truth = {
      labels: 30, findings: 12, papers: 3,
      precision: { pointer: kind(2, 2), terms: kind(8, 10), caption: kind(0, 0), similarity: kind(0, 0), all: kind(10, 12) },
      recall: { methods: 14, reached: 10, recall: 0.714 }, misses: { count: 3 }, false_links: { count: 1 }, astray: 1, outside: 0, none: 2,
      unanchored: { findings: 0, methods: 0, paragraphs: 0 }, paragraph: { named: 0, right: 0, accuracy: null, chooser: 'first_paragraph' },
    };
    expect(truthLine(t)).toBe('12 labelled · precision so far by evidence: pointer 1.00 (2/2) · terms 0.80 (8/10) · recall 0.71 (10/14) · 3 missed · 1 false link');
    expect(truthLine({ ...t, findings: 0 })).toMatch(/^Nothing labelled yet/);
  });
});
