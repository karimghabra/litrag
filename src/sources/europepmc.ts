/**
 * Europe PMC: the first source, because it is the one that gives sections.
 *
 * Search covers MEDLINE and PMC together; an open-access paper's full text
 * comes back as JATS XML; a paper's reference list is one more call. The
 * fetcher is injected so the tests read fixtures and never the network.
 */

import type { PaperInput, ReferenceInput } from '../db.ts';
import { decodeEntities } from '../xml.ts';

/** Europe PMC hands titles back with their markup escaped: "&lt;i&gt;In Vivo&lt;/i&gt;". */
export function plainText(text: string | undefined): string | undefined {
  if (text === undefined) return undefined;
  return decodeEntities(decodeEntities(text)).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim() || undefined;
}

export type Fetcher = (url: string) => Promise<{ ok: boolean; status: number; text(): Promise<string> }>;

export const EUROPE_PMC = 'https://www.ebi.ac.uk/europepmc/webservices/rest';

export interface EuropePmcHit {
  id: string;
  source: string;
  pmid?: string;
  pmcid?: string;
  doi?: string;
  title?: string;
  authorString?: string;
  journalTitle?: string;
  pubYear?: string;
  abstractText?: string;
  isOpenAccess?: string;
  citedByCount?: number;
  pubType?: string;
  /** The full record nests the same thing. */
  pubTypeList?: { pubType?: string[] };
}

export function paperFromHit(hit: EuropePmcHit): PaperInput {
  const year = Number(hit.pubYear);
  return {
    doi: hit.doi,
    pmid: hit.pmid,
    pmcid: hit.pmcid,
    title: (plainText(hit.title) ?? 'Untitled').replace(/\.$/, ''),
    year: Number.isFinite(year) && year > 0 ? year : undefined,
    journal: hit.journalTitle,
    authors: hit.authorString,
    abstract: plainText(hit.abstractText),
    source: 'europepmc',
    openAccess: hit.isOpenAccess === 'Y',
    citedByCount: hit.citedByCount,
    pubType: hit.pubType ?? hit.pubTypeList?.pubType?.join('; '),
  };
}

export interface SearchOptions {
  since?: number;
  limit?: number;
  fetcher?: Fetcher;
}

/** Search as a person would type it; `since` narrows to a year onward. */
export async function searchEuropePmc(query: string, options: SearchOptions = {}): Promise<PaperInput[]> {
  const fetcher = options.fetcher ?? defaultFetcher;
  const limit = Math.max(1, Math.min(options.limit ?? 25, 100));
  const q = options.since ? `(${query}) AND PUB_YEAR:[${options.since} TO 3000]` : query;
  const url = `${EUROPE_PMC}/search?query=${encodeURIComponent(q)}&format=json&resultType=core&pageSize=${limit}`;
  const res = await fetcher(url);
  if (!res.ok) throw new Error(`Europe PMC search failed (${res.status}).`);
  const body = JSON.parse(await res.text()) as { resultList?: { result?: EuropePmcHit[] } };
  return (body.resultList?.result ?? []).map(paperFromHit);
}

/** One paper by identifier: a DOI, a PMID, or a PMCID. */
export async function lookupEuropePmc(id: string, fetcher: Fetcher = defaultFetcher): Promise<PaperInput | null> {
  const trimmed = id.trim();
  const q = /^pmc\d+$/i.test(trimmed) ? `PMCID:${trimmed.toUpperCase()}` : /^\d+$/.test(trimmed) ? `EXT_ID:${trimmed} AND SRC:MED` : `DOI:"${trimmed}"`;
  const hits = await searchEuropePmc(q, { limit: 1, fetcher });
  return hits[0] ?? null;
}

/** Full text as JATS, or null when there is none to have. */
export async function fullTextXml(pmcid: string, fetcher: Fetcher = defaultFetcher): Promise<string | null> {
  const res = await fetcher(`${EUROPE_PMC}/${pmcid.toUpperCase()}/fullTextXML`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Europe PMC full text failed for ${pmcid} (${res.status}).`);
  const text = await res.text();
  return text.includes('<article') ? text : null;
}

export async function referencesOf(source: string, id: string, fetcher: Fetcher = defaultFetcher): Promise<ReferenceInput[]> {
  const res = await fetcher(`${EUROPE_PMC}/${source}/${id}/references?format=json&pageSize=1000`);
  if (!res.ok) throw new Error(`Europe PMC references failed for ${source}/${id} (${res.status}).`);
  const body = JSON.parse(await res.text()) as { referenceList?: { reference?: { title?: string; doi?: string; id?: string; source?: string }[] } };
  return (body.referenceList?.reference ?? [])
    .filter((r) => r.title)
    .map((r) => ({ title: r.title!, doi: r.doi, pmid: r.source === 'MED' ? r.id : undefined }));
}

export const defaultFetcher: Fetcher = async (url) => {
  const res = await fetch(url, { headers: { 'User-Agent': 'protracker-lit (research assistant; local use)' } });
  return { ok: res.ok, status: res.status, text: () => res.text() };
};

export interface Annotation {
  /** Chemicals, Gene_Proteins, Organisms, Diseases, Experimental Methods, Gene Ontology, … */
  type: string;
  exact: string;
  section: string;
  prefix?: string;
  postfix?: string;
  uri?: string;
}

/**
 * Europe PMC's text-mined terms for an open-access paper: the chemicals,
 * proteins, organisms and methods it names, with the section each sits in.
 * Free entity nodes for the graph, no model needed.
 */
export async function annotationsFor(pmcid: string, fetcher: Fetcher = defaultFetcher): Promise<Annotation[]> {
  const id = pmcid.toUpperCase().replace(/^PMC/, '');
  const res = await fetcher(`https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds?articleIds=PMC:${id}&format=JSON`);
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`Europe PMC annotations failed for ${pmcid} (${res.status}).`);
  const body = JSON.parse(await res.text()) as { annotations?: { type?: string; exact?: string; section?: string; prefix?: string; postfix?: string; tags?: { uri?: string }[] }[] }[];
  const out: Annotation[] = [];
  for (const article of body) {
    for (const a of article.annotations ?? []) {
      if (!a.type || !a.exact) continue;
      out.push({ type: a.type, exact: a.exact, section: (a.section ?? '').replace(/\s*\(.*\)$/, ''), prefix: a.prefix, postfix: a.postfix, uri: a.tags?.[0]?.uri });
    }
  }
  return out;
}
