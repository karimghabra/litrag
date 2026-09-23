/**
 * A stand-in for Europe PMC on 127.0.0.1, for the end-to-end suite: the REST `search` (a `core`
 * page for a literature search, a `lite` record for a DOI or PMCID the reader looks up), the
 * `fullTextXML` of an open paper, and the bulk area's open-access PDF. The worker is pointed at
 * it with LITRAG_EPMC_URL and LITRAG_EPMC_PDF_URL, so the suite exercises the real fetch path
 * without the network.
 *
 * Three papers, one for each way a paper can be had:
 *   - open XML: the repository's JATS fixture (PMC11278924);
 *   - an open PDF only (no full text XML): the first PDF the suite is given;
 *   - nothing open: the second PDF, which the "user" downloads by hand and drops on the window.
 */

import { createServer, type Server } from 'node:http';
import { readFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';

export interface FixturePaper {
  pmcid?: string;
  pmid?: string;
  doi: string;
  title: string;
  authors: string;
  journal: string;
  year: string;
  abstract: string;
  open: boolean;
  inEPMC: boolean;
  xml?: string; // a path: served as fullTextXML
  pdf?: string; // a path: served as the bulk area's zip (the raw PDF, which the fetch accepts)
}

export interface Fixture {
  url: string;
  pdfUrl: string;
  requests: string[];
  close(): Promise<void>;
}

function core(p: FixturePaper) {
  return {
    id: p.pmid ?? p.pmcid ?? p.doi, source: p.pmid ? 'MED' : 'PMC', pmid: p.pmid, pmcid: p.pmcid, doi: p.doi,
    title: p.title, authorString: p.authors, pubYear: p.year, abstractText: p.abstract,
    journalInfo: { journal: { title: p.journal } }, journalTitle: p.journal,
    isOpenAccess: p.open ? 'Y' : 'N', inEPMC: p.inEPMC ? 'Y' : 'N', inPMC: p.pmcid ? 'Y' : 'N', hasPDF: p.pdf ? 'Y' : 'N',
    citedByCount: 3, pubTypeList: { pubType: ['research-article', 'Journal Article'] }, pubType: 'research-article; Journal Article',
  };
}

export async function startFixture(papers: FixturePaper[]): Promise<Fixture> {
  const requests: string[] = [];
  const server: Server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1');
    requests.push(url.pathname + url.search);
    const send = (code: number, body: string | Buffer, type = 'application/json') => {
      res.writeHead(code, { 'Content-Type': type });
      res.end(body);
    };
    if (url.pathname === '/rest/search') {
      const q = url.searchParams.get('query') ?? '';
      let hits = papers;
      const doi = /DOI:"([^"]+)"/i.exec(q)?.[1];
      const pmcid = /PMCID:(PMC\d+)/i.exec(q)?.[1];
      if (doi) hits = papers.filter((p) => p.doi.toLowerCase() === doi.toLowerCase());
      else if (pmcid) hits = papers.filter((p) => p.pmcid?.toUpperCase() === pmcid.toUpperCase());
      else if (/^TITLE:/i.test(q)) hits = [];
      send(200, JSON.stringify({ hitCount: hits.length, nextCursorMark: 'END', resultList: { result: hits.map(core) } }));
      return;
    }
    const xml = /^\/rest\/(PMC\d+)\/fullTextXML$/.exec(url.pathname);
    if (xml) {
      const p = papers.find((x) => x.pmcid === xml[1] && x.xml);
      if (p) send(200, readFileSync(p.xml!), 'application/xml');
      else send(500, JSON.stringify({ error: 'not open access' }));
      return;
    }
    const pdf = /^\/pdf\/PMCxxxx\d+\/(PMC\d+)\.zip$/.exec(url.pathname);
    if (pdf) {
      const p = papers.find((x) => x.pmcid === pdf[1] && x.pdf);
      if (p) send(200, readFileSync(p.pdf!), 'application/zip');
      else send(404, 'not found', 'text/plain');
      return;
    }
    send(404, 'not found', 'text/plain');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as AddressInfo).port;
  return {
    url: `http://127.0.0.1:${port}/rest`,
    pdfUrl: `http://127.0.0.1:${port}/pdf`,
    requests,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}
