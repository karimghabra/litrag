import { describe, expect, it } from 'vitest';
import { PRESETS, authorQuery, fetchableCand, quoteAround, sqlString, worksOf } from '../src/renderer/graphtab';

describe('the rows a query returns, as works', () => {
  it('reads a work column, else a candidate id, else a paper key', () => {
    expect(worksOf(['year', 'work'], [{ year: 2017, work: 'doi:10.1/a' }, { year: 2019, work: 'cand:12' }, { year: 2020, work: null }])).toEqual(['doi:10.1/a', 'cand:12', null]);
    expect(worksOf(['cand_id', 'title'], [{ cand_id: 7, title: 't' }])).toEqual(['cand:7']);
    expect(worksOf(['key', 'title'], [{ key: 'pmid:1', title: 't' }])).toEqual(['pmid:1']);
    expect(worksOf(['n'], [{ n: 3 }])).toEqual([null]);
  });

  it('offers to fetch a candidate only while there is something to fetch', () => {
    expect(fetchableCand('cand:12', 'found')).toBe(12);
    expect(fetchableCand('cand:12', 'needs-pdf')).toBe(12);
    expect(fetchableCand('cand:12', undefined)).toBe(12); // the query did not ask for its status
    expect(fetchableCand('cand:12', 'ingested')).toBeNull();
    expect(fetchableCand('cand:12', 'dismissed')).toBeNull();
    expect(fetchableCand('doi:10.1/a', 'parsed')).toBeNull(); // held already
    expect(fetchableCand(null, 'found')).toBeNull();
  });
});

describe('the questions to start from', () => {
  it('quotes what goes into SQL, and an author the way Europe PMC prints one', () => {
    expect(sqlString("O'Brien")).toBe("'O''Brien'");
    expect(authorQuery('Akkus', 'O')).toBe('AUTH:"Akkus O"');
    expect(authorQuery('Kishore', null)).toBe('AUTH:"Kishore"');
  });

  it('every preset is one SELECT, and the author one names the author', () => {
    for (const p of PRESETS) expect(p.sql('Akkus')).toMatch(/^select\b/i);
    for (const p of PRESETS) expect(p.sql('Akkus')).not.toContain(';');
    const byAuthor = PRESETS.find((p) => p.label === 'By an author')!;
    expect(byAuthor.sql("D'Amore")).toContain("a.family = 'D''Amore'");
    expect(PRESETS.find((p) => p.label === 'Held, oldest first')!.sql()).toMatch(/order by year, published/);
  });
});

describe('a passage that cites, quoted', () => {
  it('keeps the words around the marker, not the whole paragraph', () => {
    const text = `${'Earlier work set the scene. '.repeat(10)}Threads stiffen as fibrils do [12], which we confirm. ${'More follows. '.repeat(10)}`;
    const q = quoteAround(text, '[12]', 120);
    expect(q).toContain('[12]');
    expect(q.startsWith('…') && q.endsWith('…')).toBe(true);
    expect(q.length).toBeLessThanOrEqual(122);
    expect(quoteAround('Short [1].', '[1]')).toBe('Short [1].');
    expect(quoteAround('x'.repeat(300), '[9]', 100)).toBe(`${'x'.repeat(100)}…`); // no marker found: the start
  });
});
