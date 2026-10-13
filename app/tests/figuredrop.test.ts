import { describe, expect, it } from 'vitest';
import { droppedOn, figuresFor, figuresTarget, ingestParams, knownFor } from '../src/renderer/figuredrop';

describe('a PDF dropped on a figure', () => {
  it('marks the figures of a paper read from XML, and of no other', () => {
    expect(figuresFor({ key: 'doi:10.1/B', format: 'jats' })).toBe('doi:10.1/B'); // the key as filed
    expect(figuresFor({ key: 'doi:1', format: 'pdf' })).toBeUndefined(); // its own PDF draws them: a drop there is a paper of its own
    expect(figuresFor({ key: 'doi:1', format: null })).toBeUndefined();
    expect(figuresFor(undefined)).toBeUndefined();
  });

  it('finds the paper whose figure is under the drop point, beneath the overlay', () => {
    expect(figuresTarget([{ dataset: {} }, { dataset: { figuresFor: 'doi:1' } }])).toBe('doi:1');
    expect(figuresTarget([{ dataset: {} }, { dataset: { figuresFor: 'doi:1' } }, { dataset: { figuresFor: 'doi:2' } }])).toBe('doi:1'); // the topmost
    expect(figuresTarget([{ dataset: {} }, {}, { dataset: { figuresFor: '' } }])).toBeNull(); // no figure there: filed as any drop is
    expect(figuresTarget([])).toBeNull();
  });

  it('names the paper for each PDF dropped, and for no XML', () => {
    expect(knownFor(['/a.pdf', '/b.xml'], 'doi:1')).toEqual({ '/a.pdf': { paper: 'doi:1' } });
    expect(knownFor(['/c.PDF', '/d.pdf'], 'doi:1')).toEqual({ '/c.PDF': { paper: 'doi:1' }, '/d.pdf': { paper: 'doi:1' } });
    expect(knownFor(['/b.xml'], 'doi:1')).toEqual({}); // an XML dropped on a figure is read as it would be anywhere
  });

  it('asks the worker to keep the PDFs dropped on a figure for that paper, and nothing else', () => {
    const overlay = { id: 'drop-overlay', dataset: {} };
    const placeholder = { dataset: {} }; // inside the figure
    const figure = { dataset: { figuresFor: 'pmcid:PMC1' } };
    const body = { dataset: {} };
    const known = droppedOn(['/a.pdf', '/b.xml'], [overlay, placeholder, figure, body]);
    expect(known).toEqual({ '/a.pdf': { paper: 'pmcid:PMC1' } });
    expect(ingestParams('lib', ['/a.pdf', '/b.xml'], known)).toEqual({ lib: 'lib', paths: ['/a.pdf', '/b.xml'], known: { '/a.pdf': { paper: 'pmcid:PMC1' } } });
    // dropped where no figure is, or only an XML on one: the request says nothing of the files
    for (const [paths, stack] of [[['/a.pdf'], [overlay, body]], [['/b.xml'], [overlay, placeholder, figure, body]]] as const) {
      const none = droppedOn([...paths], stack);
      expect(none).toEqual({});
      expect(ingestParams('lib', [...paths], none)).not.toHaveProperty('known');
    }
  });
});
