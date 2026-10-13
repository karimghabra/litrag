import { describe, expect, it } from 'vitest';
import { afterFetch, askable, askButton, askRun, collectLabel, collectList, collectTitle, walkable, type FiguresWanted } from '../src/renderer/collectlist';

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
      { cand_id: 0, title: 'Read as XML — the PDF, for its 6 figures', doi: '10.1/b', pmid: null, pmcid: 'PMC1', open: null, paper: 'doi:10.1/b' },
    ]);
  });

  it('opens the open copy OpenAlex named for an XML paper first, still after the candidates, and says what PMC said', () => {
    const list = collectList(
      [{ cand_id: 7, title: 'No copy', doi: '10.1/a' }],
      [
        { key: 'doi:10.1/b', title: 'Read as XML', doi: '10.1/b', pmid: null, pmcid: 'PMC1', figures: 6, figures_note: 'PMC Cloud: not in its open-access datasets; bulk area: HTTP 404', oa_url: 'https://repository.example/b.pdf' },
        { key: 'pmid:22', title: null, doi: null, pmid: '22', pmcid: null, figures: 1 },
      ],
    );
    expect(list.map((e) => e.cand_id)).toEqual([7, 0, 0]); // the candidates first, whatever the figures' entries can open
    expect(list[1]).toEqual({
      cand_id: 0, title: 'Read as XML — the PDF, for its 6 figures — PMC: PMC Cloud: not in its open-access datasets; bulk area: HTTP 404',
      doi: '10.1/b', pmid: null, pmcid: 'PMC1', open: 'https://repository.example/b.pdf', paper: 'doi:10.1/b',
    });
    expect(list[2]).toEqual({ cand_id: 0, title: 'pmid:22 — the PDF, for its 1 figure', doi: null, pmid: '22', pmcid: null, open: null, paper: 'pmid:22' });
  });

  it('walks only the XML papers with an identifier to open a page by', () => {
    const hashed = { key: 'sha:x', title: 'Nothing to open', doi: null, pmid: null, pmcid: null, figures: 2 };
    const figures = [
      { key: 'doi:10.1/b', title: 'By DOI', doi: '10.1/b', pmid: null, pmcid: null, figures: 6 },
      hashed,
      { key: 'pmid:22', title: 'By PMID', doi: null, pmid: '22', pmcid: null, figures: 1 },
      { key: 'pmcid:PMC3', title: 'By PMCID', doi: null, pmid: null, pmcid: 'PMC3', figures: 3 },
    ];
    expect(walkable(figures).map((f) => f.key)).toEqual(['doi:10.1/b', 'pmid:22', 'pmcid:PMC3']);
    expect(walkable([hashed])).toEqual([]);
    expect(collectList([], [hashed])).toEqual([]);
  });

  it('counts the XML papers PMC can be asked a PDF for: a PMCID, and no answer settled', () => {
    expect(askable([
      { key: 'doi:10.1/b', title: null, doi: '10.1/b', pmid: null, pmcid: 'PMC1', figures: 6 },
      { key: 'doi:10.1/c', title: null, doi: '10.1/c', pmid: null, pmcid: 'PMC2', figures: 2, figures_note: null },
      { key: 'doi:10.1/d', title: null, doi: '10.1/d', pmid: null, pmcid: 'PMC3', figures: 2, figures_note: 'PMC Cloud: not in its open-access datasets; bulk area: HTTP 404' }, // asked again from its figure's pane
      { key: 'doi:10.1/e', title: null, doi: '10.1/e', pmid: null, pmcid: null, figures: 4 }, // nothing to ask PMC by
    ])).toBe(2);
    expect(askable([])).toBe(0);
  });

  it('sends the PDF caught for an XML paper\'s figures to that paper, and a candidate\'s to whatever it names', () => {
    const [cand, fig] = collectList([{ cand_id: 7, title: 'No copy', doi: '10.1/a' }], [{ key: 'doi:10.1/B', title: 'Read as XML', doi: '10.1/b', pmid: null, pmcid: null, figures: 1 }]);
    expect(fig?.paper).toBe('doi:10.1/B'); // the paper's key as filed, not one made from its DOI
    expect(cand).not.toHaveProperty('paper');
  });

  it('says on the button how many want a PDF, and how many only for their figures', () => {
    expect(collectLabel(0, 0, false)).toBe('Collect PDFs');
    expect(collectLabel(3, 0, false)).toBe('Collect PDFs (3)');
    expect(collectLabel(3, 5, false)).toBe('Collect PDFs (3 + 5 for figures)');
    expect(collectLabel(0, 5, false)).toBe('Collect PDFs (5 for figures)');
    expect(collectLabel(0, 1, false)).toBe('Collect PDFs (1 for figures)');
    expect(collectLabel(3, 5, true)).toBe('Collecting…');
  });

  it('says in its hover text what it walks, and the papers for figures it cannot', () => {
    const byDoi: FiguresWanted = { key: 'doi:10.1/b', title: null, doi: '10.1/b', pmid: null, pmcid: 'PMC1', figures: 6 };
    const hashed = (n: number): FiguresWanted => ({ key: `sha:${n}`, title: null, doi: null, pmid: null, pmcid: null, figures: 2 });
    expect(collectTitle(2, [])).toBe('Open each paper’s page to fetch its PDF by hand');
    expect(collectTitle(2, [byDoi])).toBe("Open each paper's page to fetch its PDF by hand: 2 with no open copy, and 1 read from XML whose figures need the PDF to be read");
    // the label counts only what is walked (walkable), and the hover says what is left out
    expect(collectTitle(0, [byDoi, hashed(1)])).toBe("Open each paper's page to fetch its PDF by hand: 0 with no open copy, and 1 read from XML whose figures need the PDF to be read (1 more has no identifier to open a page by)");
    expect(collectTitle(0, [byDoi, hashed(1), hashed(2)])).toBe("Open each paper's page to fetch its PDF by hand: 0 with no open copy, and 1 read from XML whose figures need the PDF to be read (2 more have no identifier to open a page by)");
    // none walked: nothing counted before them to be "more" than
    expect(collectTitle(3, [hashed(1)])).toBe('Open each paper’s page to fetch its PDF by hand: 1 read from XML whose figures need the PDF has no identifier to open a page by');
    expect(collectTitle(0, [hashed(1), hashed(2)])).toBe('Open each paper’s page to fetch its PDF by hand: 2 read from XML whose figures need the PDF have no identifier to open a page by');
  });
});

describe('Ask PMC, beside Collect PDFs', () => {
  const asked: FiguresWanted = { key: 'doi:10.1/a', title: null, doi: '10.1/a', pmid: null, pmcid: 'PMC1', figures: 2, figures_note: 'PMC Cloud: not in its open-access datasets; bulk area: HTTP 404' };
  const askNow: FiguresWanted = { key: 'doi:10.1/b', title: null, doi: '10.1/b', pmid: null, pmcid: 'PMC2', figures: 6 };
  const noPmcid: FiguresWanted = { key: 'doi:10.1/c', title: null, doi: '10.1/c', pmid: null, pmcid: null, figures: 3 };

  it('counts the papers PMC can still be asked for, and is disabled at none and while it asks', () => {
    const title = 'Ask PMC, by PMCID, for the PDF of each paper read from XML whose figures want one: a PDF whose first pages name the paper is kept beside its XML, and its figures are read';
    expect(askButton([asked, askNow, noPmcid], true, false)).toEqual({ label: 'Ask PMC for 1 figure PDF', title, hidden: false, disabled: false });
    expect(askButton([asked, askNow, noPmcid], true, true).disabled).toBe(true); // one ask at a time
    expect(askButton([asked, noPmcid], true, false)).toEqual({ label: 'Ask PMC for 0 figure PDFs', title, hidden: false, disabled: true });
    expect(askButton([], true, false).disabled).toBe(true);
  });

  it('is hidden with the figures off, and shown until the worker has said', () => {
    expect(askButton([askNow], false, false).hidden).toBe(true);
    expect(askButton([askNow], true, false).hidden).toBe(false);
    expect(askButton([askNow], null, false).hidden).toBe(false);
  });

  it('is under way from the click until its own run ends, not a figure pane’s', () => {
    const run = askRun();
    expect(run.lib).toBeNull();
    run.start('lib-a');
    expect(run.lib).toBe('lib-a');
    expect(run.queued('r7')).toBe(true);
    expect(run.ended('r6')).toBe(false); // a figure pane's ask, sent before
    expect(run.ended('r7-figures')).toBe(false); // the read of what its own ask kept: its own `done` came first
    expect(run.lib).toBe('lib-a');
    expect(run.ended('r7')).toBe(true);
    expect(run.lib).toBeNull();
    expect(run.ended('r7')).toBe(false); // over already
  });

  it('is over when its own run ends before the worker’s `queued` is back, and not when another does then', () => {
    const run = askRun();
    run.start('lib-a');
    expect(run.ended('r6')).toBe(false); // a pane's, ending while this one's id is on its way
    expect(run.queued('r7')).toBe(true);
    expect(run.ended('r7')).toBe(true);
    run.start('lib-a');
    expect(run.ended('r9')).toBe(false); // its own: a run with nothing left to ask, ended before its id came
    expect(run.queued('r9')).toBe(false);
    expect(run.lib).toBeNull();
  });

  it('is over when its request fails, or is answered with no id', () => {
    const run = askRun();
    run.start('lib-a');
    expect(run.queued('r10')).toBe(true);
    run.failed();
    expect(run.lib).toBeNull();
    run.start('lib-a');
    expect(run.queued(undefined)).toBe(false);
    expect(run.lib).toBeNull();
  });
});

describe('what a fetch goes on to', () => {
  const candidates = [
    { cand_id: 1, paper_key: 'doi:10.1/a' },
    { cand_id: 2, paper_key: 'doi:10.1/b' },
    { cand_id: 3, paper_key: 'sha:c' },
    { cand_id: 4, paper_key: null },
    { cand_id: 5, paper_key: 'doi:10.1/e' },
  ];
  const wanted = (key: string, doi: string | null): FiguresWanted => ({ key, title: null, doi, pmid: null, pmcid: null, figures: 2 });
  const figures = [wanted('doi:10.1/a', '10.1/a'), wanted('doi:10.1/b', '10.1/b'), wanted('sha:c', null), wanted('doi:10.1/e', '10.1/e')];

  it('opens the collect window for papers with no open copy, and only for those', () => {
    expect(afterFetch([{ cand_id: 1, status: 'ingested' }, { cand_id: 4, status: 'needs-pdf' }], candidates, figures, false)).toEqual({ open: 1 });
  });

  it('never opens it for papers read from XML that want a PDF for their figures: it counts them (K15)', () => {
    // only this fetch's papers, and only those the window can walk (a hash-keyed paper has no page to open)
    expect(afterFetch([{ cand_id: 1, status: 'ingested' }, { cand_id: 2, status: 'ingested' }, { cand_id: 3, status: 'ingested' }], candidates, figures, false)).toEqual({ figures: 2 });
    expect(afterFetch([{ cand_id: 1, status: 'ingested' }], candidates, figures, false)).toEqual({ figures: 1 }); // doi:10.1/b and /e want one too, from an earlier fetch
    expect(afterFetch([{ cand_id: 4, status: 'failed' }], candidates, figures, false)).toEqual({ figures: 0 });
    expect(afterFetch([], candidates, figures, false)).toEqual({ figures: 0 });
  });

  it('counts them when the window is open already, with papers with no open copy too', () => {
    expect(afterFetch([{ cand_id: 4, status: 'needs-pdf' }, { cand_id: 5, status: 'ingested' }], candidates, figures, true)).toEqual({ figures: 1 });
  });
});
