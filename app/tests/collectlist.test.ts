import { describe, expect, it } from 'vitest';
import { collectLabel, collectList } from '../src/renderer/collectlist';

describe('what the collect window walks', () => {
  it('opens the papers with no copy first, then the XML papers wanting a PDF for their figures', () => {
    const list = collectList(
      [{ cand_id: 7, title: 'No copy', doi: '10.1/a', pmid: null, pmcid: null }, { cand_id: 8, title: 'An open copy elsewhere', doi: '10.1/c', oa_url: 'https://repository.example/c.pdf' }],
      [
        { key: 'doi:10.1/b', title: 'Read as XML', doi: '10.1/b', pmid: null, pmcid: 'PMC1', figures: 6 },
        { key: 'sha:x', title: 'Nothing to open', doi: null, pmid: null, pmcid: null, figures: 2 },
      ],
    );
    expect(list).toEqual([
      { cand_id: 7, title: 'No copy', doi: '10.1/a', pmid: null, pmcid: null, open: null },
      { cand_id: 8, title: 'An open copy elsewhere', doi: '10.1/c', pmid: null, pmcid: null, open: 'https://repository.example/c.pdf' }, // the window opens it first
      { cand_id: 0, title: 'Read as XML — the PDF, for its 6 figures', doi: '10.1/b', pmid: null, pmcid: 'PMC1' },
    ]);
  });

  it('says on the button how many want a PDF, and how many only for their figures', () => {
    expect(collectLabel(0, 0, false)).toBe('Collect PDFs');
    expect(collectLabel(3, 0, false)).toBe('Collect PDFs (3)');
    expect(collectLabel(3, 5, false)).toBe('Collect PDFs (3 + 5 for figures)');
    expect(collectLabel(0, 5, false)).toBe('Collect PDFs (5 for figures)');
    expect(collectLabel(3, 5, true)).toBe('Collecting…');
  });
});
