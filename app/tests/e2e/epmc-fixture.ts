/**
 * A stand-in for Europe PMC on 127.0.0.1, for the end-to-end suite: the REST `search` (a `core`
 * page for a literature search, a `lite` record for a DOI or PMCID the reader looks up), the
 * `fullTextXML` of an open paper, and the bulk area's open-access PDF. The worker is pointed at
 * it with LITRAG_EPMC_URL and LITRAG_EPMC_PDF_URL, so the suite exercises the real fetch path
 * without the network; LITRAG_NCBI_URL points NCBI's E-utilities here too, where every `efetch`
 * is not found, LITRAG_PMC_CLOUD_URL points the PMC Cloud Service here, whose every listing is
 * empty, and LITRAG_OPENALEX_URL points OpenAlex here, where no work is found, so no identifier in
 * the suite is ever asked of the real NCBI, NLM or OpenAlex.
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
  download?: string; // a path: the PDF its publisher's page (/doi/<doi>) links to, for the collect window
}

export interface Fixture {
  url: string;
  pdfUrl: string;
  doiUrl: string;
  ncbiUrl: string;
  cloudUrl: string;
  openalexUrl: string;
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
    if (url.pathname === '/cloud/' && url.searchParams.get('list-type') === '2') {
      const prefix = url.searchParams.get('prefix') ?? '';
      send(200, `<?xml version="1.0" encoding="UTF-8"?>\n<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>pmc-oa-opendata</Name><Prefix>${prefix}</Prefix><KeyCount>0</KeyCount><Delimiter>/</Delimiter><IsTruncated>false</IsTruncated></ListBucketResult>`, 'application/xml');
      return;
    }
    // a publisher's page for a paper, by DOI (LITRAG_DOI_RESOLVER points the collect window here),
    // with a link to its PDF — which, served as an attachment, is what a person's click downloads
    const landing = /^\/doi\/(.+)$/.exec(url.pathname);
    if (landing) {
      const doi = decodeURIComponent(landing[1]!);
      const p = papers.find((x) => x.doi.toLowerCase() === doi.toLowerCase());
      if (!p) return send(404, 'no such DOI', 'text/plain');
      const pdfLink = p.download ? `<a id="pdf" href="/files/${encodeURIComponent(p.doi)}.pdf">Download PDF</a>` : '<p>Sign in to read this article.</p>';
      return send(200, `<!doctype html><html><head><title>${p.title}</title></head><body><h1>${p.title}</h1>${pdfLink}</body></html>`, 'text/html');
    }
    const file = /^\/files\/(.+)\.pdf$/.exec(url.pathname);
    if (file) {
      const p = papers.find((x) => x.doi.toLowerCase() === decodeURIComponent(file[1]!).toLowerCase() && x.download);
      if (!p) return send(404, 'not found', 'text/plain');
      res.writeHead(200, { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="article.pdf"' });
      res.end(readFileSync(p.download!));
      return;
    }
    send(404, 'not found', 'text/plain');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as AddressInfo).port;
  return {
    url: `http://127.0.0.1:${port}/rest`,
    pdfUrl: `http://127.0.0.1:${port}/pdf`,
    doiUrl: `http://127.0.0.1:${port}/doi/`,
    ncbiUrl: `http://127.0.0.1:${port}/ncbi`,
    cloudUrl: `http://127.0.0.1:${port}/cloud`,
    openalexUrl: `http://127.0.0.1:${port}/openalex`,
    requests,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}
