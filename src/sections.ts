/**
 * What kind of section a heading names, and sections out of page text.
 *
 * JATS gives sections; a PDF gives pages. This finds the headings a paper
 * almost always has and cuts the text at them, dropping everything from the
 * references on — a reference list is not something to answer questions from.
 */

import type { SectionInput } from './db.ts';

const KINDS: [RegExp, string][] = [
  [/^abstract\b/i, 'abstract'],
  [/^(introduction|background)\b/i, 'introduction'],
  [/^(results?(\s+and\s+discussion)?|experimental\s+results?(\s+and\s+discussion)?|findings)\b/i, 'results'],
  [/^(materials?\s+and\s+methods?|methods?|experimental(\s+(section|procedures?|methods?))?|methodology)\b/i, 'methods'],
  [/^(discussion|conclusions?|summary|outlook|limitations)\b/i, 'discussion'],
  [/^(references?|bibliography|literature\s+cited|works\s+cited)\b/i, 'references'],
  [/^(acknowledg\w*|funding|author\s+contributions?|conflicts?\s+of\s+interest|data\s+availability|supplementary|appendix)\b/i, 'back'],
];

/** A heading's kind — the first word of its own title, parents stripped. */
export function kindOf(heading: string): string {
  const own = heading.split(' › ').pop() ?? heading;
  const clean = own.replace(/^\s*\d+(\.\d+)*\.?\s+/, '').trim();
  for (const [re, kind] of KINDS) if (re.test(clean)) return kind;
  // A subsection inherits its parent's kind: "Methods › Cell culture" is methods.
  const parents = heading.split(' › ').slice(0, -1);
  for (const parent of parents.reverse()) {
    const p = parent.replace(/^\s*\d+(\.\d+)*\.?\s+/, '').trim();
    for (const [re, kind] of KINDS) if (re.test(p)) return kind;
  }
  return 'other';
}

const HEADING = /^\s*(\d+(\.\d+)*\.?\s+)?(abstract|introduction|background|materials?\s+and\s+methods?|methods?|experimental(\s+(section|procedures?|methods?))?|results?(\s+and\s+discussion)?|discussion|conclusions?|references?|bibliography|acknowledg\w*|funding|supplementary\s+\w*|author\s+contributions?|conflicts?\s+of\s+interest|data\s+availability)\s*:?\s*$/i;

/**
 * Sections from a PDF's pages (each page a string of lines). The text before
 * the first heading is the front matter — title, authors, and usually the
 * abstract when the word "Abstract" is not on its own line — and is kept as
 * "Front matter" so nothing said in it is lost.
 */
export function sectionsFromPages(pages: string[]): SectionInput[] {
  const lines: { text: string; page: number }[] = [];
  pages.forEach((page, i) => {
    for (const line of page.split('\n')) lines.push({ text: line, page: i + 1 });
  });
  const out: SectionInput[] = [];
  let heading = 'Front matter';
  let kind = 'front';
  let page = 1;
  let buffer: string[] = [];
  const flush = () => {
    const text = joinLines(buffer);
    if (text) out.push({ heading, kind, text, page });
    buffer = [];
  };
  for (const line of lines) {
    const trimmed = line.text.trim();
    if (trimmed.length <= 60 && HEADING.test(trimmed)) {
      flush();
      heading = trimmed.replace(/^\s*\d+(\.\d+)*\.?\s+/, '').replace(/:$/, '');
      kind = kindOf(heading);
      page = line.page;
      continue;
    }
    buffer.push(line.text);
  }
  flush();
  // Everything from the references on is not the paper's own prose.
  const end = out.findIndex((s) => s.kind === 'references');
  return (end < 0 ? out : out.slice(0, end)).filter((s) => s.kind !== 'back');
}

/**
 * Lines back into paragraphs: a hyphen at a line end joins the word, a blank
 * line ends the paragraph, everything else is one running text.
 */
function joinLines(lines: string[]): string {
  const paragraphs: string[] = [];
  let current = '';
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      if (current) paragraphs.push(current);
      current = '';
      continue;
    }
    if (current.endsWith('-') && /^[a-z]/.test(line)) current = current.slice(0, -1) + line;
    else current = current ? `${current} ${line}` : line;
  }
  if (current) paragraphs.push(current);
  return paragraphs.join('\n\n').replace(/[ \t]+/g, ' ').trim();
}
