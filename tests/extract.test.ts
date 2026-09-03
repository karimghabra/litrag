import { describe, expect, it } from 'vitest';
import { chunkSections, MAX_WORDS, splitSentences } from '../src/chunk.ts';
import { mineParameters } from '../src/parameters.ts';
import { sectionsFromPages } from '../src/sections.ts';
import { findDoi } from '../src/pdf.ts';

describe('the parameter miner', () => {
  it('finds every number with a unit, and the sentence it lives in', () => {
    const found = mineParameters(
      'Threads were crosslinked in 10 mM EDC and 5 mM NHS at 4 °C for 24 h at pH 5.5. ' +
        'Samples were then stretched to 12% strain at 0.5 mm/min. In 2019 we used 2 mg/mL collagen.',
    );
    const by = (unit: string) => found.filter((p) => p.unit === unit).map((p) => p.valueNum);
    expect(by('mM')).toEqual([10, 5]);
    expect(by('°C')).toEqual([4]);
    expect(by('h')).toEqual([24]);
    expect(by('pH')).toEqual([5.5]);
    expect(by('%')).toEqual([12]);
    expect(by('mm/min')).toEqual([0.5]);
    expect(by('mg/mL')).toEqual([2]);
    expect(found.find((p) => p.unit === 'mM')?.sentence).toContain('crosslinked');
    expect(found.find((p) => p.unit === 'mM')?.kind).toBe('concentration');
    expect(by('s')).toEqual([]);
  });

  it('keeps ranges and tolerances whole', () => {
    const found = mineParameters('Fibres measured 50–120 µm across and were loaded to 1.2 ± 0.3 N.');
    expect(found.map((p) => [p.value, p.unit])).toEqual([
      ['50–120', 'µm'],
      ['1.2 ± 0.3', 'N'],
    ]);
  });
});

describe('chunking', () => {
  const sentence = (i: number) => `Sentence number ${i} says something about collagen threads and their alignment under load.`;
  const text = Array.from({ length: 90 }, (_, i) => sentence(i)).join(' ');

  it('splits at sentences', () => {
    expect(splitSentences('One thing. Another (thing). A third: 3.5 mM was used. Done')).toHaveLength(4);
  });

  it('never cuts a sentence and overlaps the seam by one', () => {
    const chunks = chunkSections([{ heading: 'Methods', kind: 'methods', text }]);
    expect(chunks.length).toBeGreaterThan(2);
    for (const c of chunks) {
      expect(c.words).toBeLessThanOrEqual(MAX_WORDS);
      expect(c.text.endsWith('.')).toBe(true);
    }
    const first = chunks[0]!.text;
    const lastSentence = first.slice(first.lastIndexOf('Sentence number'));
    expect(chunks[1]!.text.startsWith(lastSentence)).toBe(true);
    expect(chunks.map((c) => c.ordinal)).toEqual(chunks.map((_, i) => i));
  });
});

describe('sections out of pages', () => {
  it('cuts at the headings a paper has, joins hyphenated lines, drops the references', () => {
    const pages = [
      'A Study of Threads\nA. Author, B. Author\nAbstract\nWe made threads.\n1. Introduction\nThreads are inter-\nesting.\n',
      '2. Materials and Methods\nCollagen at 2 mg/mL.\n\nA second paragraph.\n3. Results\nThey held.\nReferences\n1. Someone, 2019.\n',
    ];
    const sections = sectionsFromPages(pages);
    expect(sections.map((s) => s.kind)).toEqual(['front', 'abstract', 'introduction', 'methods', 'results']);
    expect(sections[2]!.text).toBe('Threads are interesting.');
    expect(sections[3]!.text).toBe('Collagen at 2 mg/mL.\n\nA second paragraph.');
    expect(sections[3]!.page).toBe(2);
    expect(sections[0]!.page).toBe(1);
  });

  it('finds a DOI in front matter', () => {
    expect(findDoi('Micromachines 2024, 15, 851. https://doi.org/10.3390/mi15070851. Received')).toBe('10.3390/mi15070851');
    expect(findDoi('no doi here')).toBeUndefined();
  });
});
