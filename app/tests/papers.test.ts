import { describe, expect, it } from 'vitest';
import { bandOf, countBy, filterPapers, formatOf, sortPapers, validFilters } from '../src/renderer/papers';

const papers = [
  { key: 'doi:1', title: 'Tendon scaffolds', format: 'pdf', type: 'research', year: '2019', status: 'parsed', confidence: 0.41 },
  { key: 'doi:2', title: 'a review of collagen', format: 'jats', type: 'review', year: '2024', status: 'parsed', confidence: 1 },
  { key: 'doi:3', title: 'Bioglass in tendon repair', format: 'jats', type: 'research', year: null, status: 'parsed', confidence: 0.93 },
  { key: 'doi:4', title: 'Case of a ruptured tendon', format: 'pdf', type: 'case-report', year: '2021', status: 'parsed', confidence: 0.72 },
  { key: 'sha:5', title: '', file: 'sha_5.pdf', format: null, type: null, year: null, status: 'parsing' },
];
const keys = (list: { key: string }[]) => list.map((p) => p.key);

describe('the paper list', () => {
  it('calls the JATS a publisher deposits XML, and reads the format off the file when the row has none', () => {
    expect(papers.map(formatOf)).toEqual(['pdf', 'xml', 'xml', 'pdf', 'pdf']);
    expect(formatOf({ key: 'k', file: 'notes.txt' })).toBe('unknown');
  });

  it('sorts by format with PDFs together and XML together, each in the order they were added', () => {
    expect(keys(sortPapers(papers, 'format'))).toEqual(['doi:1', 'doi:4', 'sha:5', 'doi:2', 'doi:3']);
    expect(keys(sortPapers(papers, 'added'))).toEqual(keys(papers));
    expect(keys(papers)).toEqual(['doi:1', 'doi:2', 'doi:3', 'doi:4', 'sha:5']); // the input is left alone
  });

  it('sorts by type, title and year', () => {
    expect(keys(sortPapers(papers, 'type'))).toEqual(['doi:1', 'doi:3', 'doi:2', 'doi:4', 'sha:5']); // research, review, case report, then the unread
    expect(keys(sortPapers(papers, 'title'))).toEqual(['doi:2', 'doi:3', 'doi:4', 'sha:5', 'doi:1']); // case-blind; an untitled paper by its file
    expect(keys(sortPapers(papers, 'year'))).toEqual(['doi:2', 'doi:4', 'doi:1', 'doi:3', 'sha:5']); // newest first, the undated last
  });

  it('filters by type and by format, and never hides a paper that is still being read behind a type', () => {
    expect(keys(filterPapers(papers, { format: null, type: 'research' }))).toEqual(['doi:1', 'doi:3', 'sha:5']);
    expect(keys(filterPapers(papers, { format: 'xml', type: null }))).toEqual(['doi:2', 'doi:3']);
    expect(keys(filterPapers(papers, { format: 'xml', type: 'research' }))).toEqual(['doi:3']);
    expect(keys(filterPapers(papers, { format: null, type: null }))).toEqual(keys(papers));
  });

  it('sorts the least trusted readings first and narrows to a band of confidence', () => {
    expect(keys(sortPapers(papers, 'confidence'))).toEqual(['doi:1', 'doi:4', 'doi:3', 'doi:2', 'sha:5']); // a paper with no score yet goes last
    expect(papers.map(bandOf)).toEqual(['low', 'high', 'high', 'middle', null]);
    expect(keys(filterPapers(papers, { format: null, type: null, band: 'high' }))).toEqual(['doi:2', 'doi:3', 'sha:5']); // the unread paper stays in view
    expect(countBy(papers, 'band')).toEqual([['high', 2], ['middle', 1], ['low', 1]]);
    expect(validFilters(papers, { format: null, type: null, band: 'low' }).band).toBe('low');
    expect(validFilters(papers.slice(1), { format: null, type: null, band: 'low' }).band).toBeNull();
  });

  it('counts what the chips show, and drops a filter no paper answers to', () => {
    expect(countBy(papers, 'format')).toEqual([['pdf', 3], ['xml', 2]]);
    expect(countBy(papers, 'type')).toEqual([['research', 2], ['review', 1], ['case-report', 1]]);
    expect(validFilters(papers, { format: 'xml', type: 'letter' })).toEqual({ format: 'xml', type: null, band: null });
    expect(validFilters([], { format: 'pdf', type: 'review' })).toEqual({ format: null, type: null, band: null });
  });
});
