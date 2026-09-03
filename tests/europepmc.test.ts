import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { fullTextXml, lookupEuropePmc, plainText, referencesOf, searchEuropePmc, type Fetcher } from '../src/sources/europepmc.ts';

const fixture = (name: string) => readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), 'utf8');

function fetcherOf(routes: Record<string, { status: number; body: string }>, seen: string[] = []): Fetcher {
  return async (url) => {
    seen.push(url);
    const hit = Object.entries(routes).find(([needle]) => url.includes(needle));
    const { status, body } = hit ? hit[1] : { status: 404, body: '' };
    return { ok: status >= 200 && status < 300, status, text: async () => body };
  };
}

describe('Europe PMC', () => {
  it('reads a title the way a person would, markup and all', () => {
    expect(plainText('&lt;i&gt;In Vivo&lt;/i&gt; Delivery of M0 Macrophages')).toBe('In Vivo Delivery of M0 Macrophages');
    expect(plainText('Plain &amp; simple')).toBe('Plain & simple');
    expect(plainText(undefined)).toBeUndefined();
  });

  it('turns search hits into papers, and says what it asked for', async () => {
    const seen: string[] = [];
    const papers = await searchEuropePmc('"electrochemically aligned collagen"', {
      since: 2020,
      limit: 5,
      fetcher: fetcherOf({ '/search?': { status: 200, body: fixture('europepmc-search.json') } }, seen),
    });
    expect(papers).toHaveLength(5);
    expect(papers[0]).toMatchObject({ pmid: '35647785', pmcid: 'PMC10170307', year: 2022, source: 'europepmc', openAccess: false });
    expect(papers.find((p) => p.pmcid === 'PMC11278924')?.openAccess).toBe(true);
    expect(papers[0]?.pubType).toContain('journal article');
    expect(seen[0]).toContain('resultType=core');
    expect(seen[0]).toContain(encodeURIComponent('PUB_YEAR:[2020 TO 3000]'));
    expect(seen[0]).toContain('pageSize=5');
  });

  it('looks a paper up by DOI, PMID or PMCID with the right query', async () => {
    const seen: string[] = [];
    const fetcher = fetcherOf({ '/search?': { status: 200, body: fixture('europepmc-search.json') } }, seen);
    await lookupEuropePmc('10.3390/mi15070851', fetcher);
    await lookupEuropePmc('39064362', fetcher);
    await lookupEuropePmc('pmc11278924', fetcher);
    expect(decodeURIComponent(seen[0]!)).toContain('DOI:"10.3390/mi15070851"');
    expect(decodeURIComponent(seen[1]!)).toContain('EXT_ID:39064362 AND SRC:MED');
    expect(decodeURIComponent(seen[2]!)).toContain('PMCID:PMC11278924');
  });

  it('hands back full text, or null when there is none', async () => {
    const fetcher = fetcherOf({ 'PMC11278924/fullTextXML': { status: 200, body: fixture('PMC11278924.xml') } });
    expect((await fullTextXml('PMC11278924', fetcher))?.length).toBeGreaterThan(10_000);
    expect(await fullTextXml('PMC1', fetcher)).toBeNull();
  });

  it('lists references with their identifiers', async () => {
    const refs = await referencesOf('MED', '39064362', fetcherOf({ '/references?': { status: 200, body: fixture('europepmc-references.json') } }));
    expect(refs.length).toBeGreaterThan(0);
    expect(refs.every((r) => r.title)).toBe(true);
    expect(refs.some((r) => r.pmid || r.doi)).toBe(true);
  });
});
