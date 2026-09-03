/**
 * Sections into chunks the embedder can hold and a citation can point at.
 *
 * About 250 words, never split mid-sentence, with the last sentence of one
 * chunk repeated at the head of the next so a claim that straddles the cut
 * is whole in at least one of them. A chunk remembers its section and page.
 */

import type { ChunkInput, SectionInput } from './db.ts';

export const TARGET_WORDS = 250;
export const MAX_WORDS = 380;

export function splitSentences(text: string): string[] {
  const out: string[] = [];
  for (const paragraph of text.split(/\n{2,}/)) {
    const parts = paragraph
      .split(/(?<=[.!?])\s+(?=[A-Z0-9("[])/)
      .map((s) => s.trim())
      .filter(Boolean);
    out.push(...parts);
  }
  return out;
}

const words = (s: string) => s.split(/\s+/).filter(Boolean).length;

export function chunkSections(sections: SectionInput[]): ChunkInput[] {
  const out: ChunkInput[] = [];
  sections.forEach((section, sectionOrdinal) => {
    const sentences = splitSentences(section.text);
    let current: string[] = [];
    let count = 0;
    let ordinal = 0;
    const flush = (carry: string | undefined) => {
      if (!current.length) return;
      const text = current.join(' ');
      out.push({ sectionOrdinal, ordinal: ordinal++, text, words: count, page: section.page });
      current = carry ? [carry] : [];
      count = carry ? words(carry) : 0;
    };
    for (const sentence of sentences) {
      const n = words(sentence);
      if (count && count + n > TARGET_WORDS) {
        const last = current[current.length - 1];
        // An overlap only earns its place when it is short: a whole-paragraph
        // sentence repeated would be most of the next chunk.
        flush(last && words(last) <= 60 ? last : undefined);
      }
      current.push(sentence);
      count += n;
      if (count >= MAX_WORDS) flush(undefined);
    }
    flush(undefined);
  });
  return out;
}
