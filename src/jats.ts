/**
 * A JATS article as the store wants it: metadata, sections, references.
 *
 * Europe PMC's full text arrives already sectioned, which is the whole reason
 * it is the first source: no heading heuristics, no page breaks in the middle
 * of a sentence. Figures, tables and formulae are left out of the text —
 * their captions are prose, but their bodies are not.
 */

import { kindOf } from './sections.ts';
import { childElements, findAll, findFirst, isElement, parseXml, textOf, type XmlElement } from './xml.ts';
import type { ReferenceInput, SectionInput } from './db.ts';

export interface JatsArticle {
  title: string;
  doi?: string;
  pmid?: string;
  pmcid?: string;
  year?: number;
  journal?: string;
  authors?: string;
  abstract?: string;
  sections: SectionInput[];
  references: ReferenceInput[];
}

/** Prose with its citation markers gone leaves "()" and "[]" behind; take those too. */
export function prose(node: XmlElement | string): string {
  return textOf(node, NOT_PROSE)
    .replace(/\s*[([][\s,;–-]*[)\]]/g, '')
    .replace(/\s+([,.;:])/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/** Elements whose text is not prose: their bodies are skipped, their captions kept. */
const NOT_PROSE = new Set(['xref', 'fig', 'table-wrap', 'disp-formula', 'inline-formula', 'supplementary-material', 'graphic', 'media', 'object-id', 'label', 'tex-math', 'mml:math']);

export function parseJats(xml: string): JatsArticle {
  const doc = parseXml(xml);
  const meta = findFirst(doc, 'article-meta');
  const ids: Record<string, string> = {};
  if (meta) {
    for (const id of childElements(meta, 'article-id')) {
      const type = id.attrs['pub-id-type'];
      if (type) ids[type] = textOf(id);
    }
  }
  const titleEl = meta ? findFirst(meta, 'article-title') : undefined;
  const journalEl = findFirst(doc, 'journal-title');
  const year = meta ? firstYear(meta) : undefined;
  const authors = meta ? authorsOf(meta) : undefined;
  const abstractEl = meta ? findFirst(meta, 'abstract') : undefined;
  // An abstract is its paragraphs; its own <title> ("Abstract") is not prose.
  const abstract = abstractEl
    ? (childElements(abstractEl, 'p').map(prose).filter(Boolean).join('\n\n') || prose(abstractEl).replace(/^Abstract\s*/i, '')) || undefined
    : undefined;

  const sections: SectionInput[] = [];
  if (abstract) sections.push({ heading: 'Abstract', kind: 'abstract', text: abstract });
  const body = findFirst(doc, 'body');
  if (body) collectSections(body, [], sections);

  // Europe PMC files the reference list as a section of the body, not in
  // <back>; the list element is the same either way.
  const references: ReferenceInput[] = [];
  for (const refList of findAll(doc, 'ref-list')) {
    for (const ref of findAll(refList, 'ref')) {
      const citation = findFirst(ref, 'element-citation') ?? findFirst(ref, 'mixed-citation') ?? ref;
      const title = findFirst(citation, 'article-title') ?? findFirst(citation, 'source') ?? findFirst(citation, 'chapter-title');
      const whole = textOf(citation, new Set(['label']));
      const entry: ReferenceInput = { title: title ? textOf(title) : whole.slice(0, 300) };
      for (const pubId of findAll(citation, 'pub-id')) {
        const type = pubId.attrs['pub-id-type'];
        if (type === 'doi') entry.doi = textOf(pubId);
        if (type === 'pmid') entry.pmid = textOf(pubId);
      }
      if (!entry.doi) {
        const m = whole.match(/doi:?\s*(10\.\d{4,9}\/[^\s"<>]+)/i);
        if (m) entry.doi = m[1]!.replace(/[.,;]+$/, '');
      }
      if (entry.title) references.push(entry);
    }
  }

  return {
    title: titleEl ? textOf(titleEl) : 'Untitled',
    doi: ids['doi'],
    pmid: ids['pmid'],
    pmcid: ids['pmcid'],
    year,
    journal: journalEl ? textOf(journalEl) : undefined,
    authors,
    abstract,
    sections,
    references,
  };
}

function firstYear(meta: XmlElement): number | undefined {
  for (const date of childElements(meta, 'pub-date')) {
    const year = findFirst(date, 'year');
    if (year) {
      const n = Number(textOf(year));
      if (Number.isFinite(n)) return n;
    }
  }
  return undefined;
}

function authorsOf(meta: XmlElement): string | undefined {
  const names: string[] = [];
  for (const group of childElements(meta, 'contrib-group')) {
    for (const contrib of childElements(group, 'contrib')) {
      if (contrib.attrs['contrib-type'] && contrib.attrs['contrib-type'] !== 'author') continue;
      const name = findFirst(contrib, 'name');
      if (!name) continue;
      const surname = findFirst(name, 'surname');
      const given = findFirst(name, 'given-names');
      names.push([surname ? textOf(surname) : '', given ? textOf(given) : ''].filter(Boolean).join(' '));
    }
  }
  return names.length ? names.join(', ') : undefined;
}

/**
 * Sections flattened in reading order. A nested section keeps its parents'
 * headings in its own ("Materials and Methods › Preparation of threads"), so
 * a chunk's citation says where in the paper it came from.
 */
function collectSections(node: XmlElement, path: string[], out: SectionInput[]): void {
  const own: string[] = [];
  let heading = path.join(' › ');
  for (const child of node.children) {
    if (!isElement(child)) continue;
    // The section's own title is already the last step of `path`.
    if (child.name === 'title' && node.name === 'sec') continue;
    if (child.name === 'sec') {
      flush();
      const title = childElements(child, 'title')[0];
      collectSections(child, title ? [...path, cleanHeading(textOf(title, NOT_PROSE))] : path, out);
      continue;
    }
    if (child.name === 'p' || child.name === 'list' || child.name === 'disp-quote' || child.name === 'boxed-text') {
      const text = prose(child);
      if (text) own.push(text);
      continue;
    }
    if (child.name === 'fig' || child.name === 'table-wrap') {
      const caption = findFirst(child, 'caption');
      if (caption) {
        const text = prose(caption);
        if (text) own.push(text);
      }
    }
  }
  flush();

  function flush(): void {
    if (!own.length) return;
    const h = heading || (path.length ? path.join(' › ') : 'Body');
    out.push({ heading: h, kind: kindOf(h), text: own.join('\n\n') });
    own.length = 0;
  }
}

function cleanHeading(text: string): string {
  return text.replace(/^\s*\d+(\.\d+)*\.?\s+/, '').trim();
}
