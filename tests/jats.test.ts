import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { parseJats } from '../src/jats.ts';
import { kindOf } from '../src/sections.ts';
import { parseXml, textOf } from '../src/xml.ts';

const fixture = readFileSync(fileURLToPath(new URL('./fixtures/PMC11278924.xml', import.meta.url)), 'utf8');

describe('the XML reader', () => {
  it('reads elements, attributes, entities and CDATA without a DOM', () => {
    const doc = parseXml('<a x="1" y=\'two\'><b>caf&#233; &amp; <![CDATA[<raw>]]></b><c/></a>');
    const a = doc.children[0];
    expect(typeof a).toBe('object');
    if (!a || typeof a === 'string') return;
    expect(a.attrs).toEqual({ x: '1', y: 'two' });
    expect(a.children).toHaveLength(2);
    expect(textOf(a)).toBe('café & <raw>');
  });
});

describe('a JATS article', () => {
  const article = parseJats(fixture);

  it('carries its identifiers and front matter', () => {
    expect(article.title).toContain('Aligned Collagen');
    expect(article.doi).toBe('10.3390/mi15070851');
    expect(article.pmcid).toBe('PMC11278924');
    expect(article.pmid).toBe('39064362');
    expect(article.year).toBe(2024);
    expect(article.journal).toBe('Micromachines');
    expect(article.authors).toContain('Lin');
    expect(article.abstract?.length).toBeGreaterThan(200);
    expect(article.abstract?.startsWith('Abstract')).toBe(false);
  });

  it('is sectioned in reading order, nested headings joined, kinds derived', () => {
    expect(article.sections[0]).toMatchObject({ heading: 'Abstract', kind: 'abstract' });
    const methods = article.sections.filter((s) => s.kind === 'methods');
    expect(methods.length).toBeGreaterThan(3);
    expect(methods.some((s) => s.heading.includes(' › '))).toBe(true);
    expect(article.sections.some((s) => s.kind === 'results')).toBe(true);
    expect(article.sections.some((s) => s.kind === 'discussion')).toBe(true);
    for (const s of article.sections) {
      expect(s.text).not.toMatch(/<\/?[a-z-]+>/);
      expect(s.text).not.toMatch(/\(\s*\)|\[[\s,]*\]/);
      expect(s.text.length).toBeGreaterThan(0);
      const steps = s.heading.split(' › ');
      expect(new Set(steps).size).toBe(steps.length);
    }
  });

  it('lists what the paper cites, with DOIs where the paper gave them', () => {
    expect(article.references.length).toBeGreaterThan(20);
    expect(article.references.some((r) => r.doi)).toBe(true);
    expect(article.references.every((r) => r.title.length > 0)).toBe(true);
  });
});

describe('the kind of a heading', () => {
  it('reads the numbered, the nested, and the unfamiliar', () => {
    expect(kindOf('2. Materials and Methods')).toBe('methods');
    expect(kindOf('Materials and Methods › 2.2. Preparation of threads')).toBe('methods');
    expect(kindOf('3. Experimental Results')).toBe('results');
    expect(kindOf('Results and Discussion')).toBe('results');
    expect(kindOf('Conclusions')).toBe('discussion');
    expect(kindOf('References')).toBe('references');
    expect(kindOf('Author Contributions')).toBe('back');
    expect(kindOf('Something else entirely')).toBe('other');
  });
});
