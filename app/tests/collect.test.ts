import { describe, expect, it, vi } from 'vitest';
import { caughtKnown } from '../src/main/collect';
import { collectList } from '../src/renderer/collectlist';

vi.mock('electron', () => ({ BrowserWindow: class {}, Menu: {}, session: {} })); // the window is not opened here

describe('a file the collect window catches', () => {
  it('goes to the XML paper it was caught for, and a candidate\'s to whatever it names', () => {
    const [cand, fig] = collectList(
      [{ cand_id: 7, title: 'No copy', doi: '10.1/a', pmid: '11' }],
      [{ key: 'pmcid:PMC1', title: 'Read as XML', doi: null, pmid: '22', pmcid: 'PMC1', figures: 3 }],
    );
    // the entry the window walks, as the worker is told of what was caught for it
    expect(caughtKnown(fig!)).toEqual({ doi: null, pmid: '22', pmcid: 'PMC1', paper: 'pmcid:PMC1' });
    expect(caughtKnown(cand!)).toEqual({ doi: '10.1/a', pmid: '11', pmcid: null, paper: null }); // filed by its identifiers, as before
  });
});
