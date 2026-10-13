import { describe, expect, it } from 'vitest';
import { arrived, bandOf, countBy, filedLine, filterPapers, formatOf, readFiguresOf, sortPapers, validFilters } from '../src/renderer/papers';

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

describe('a PDF kept beside an XML paper, as the log and the figure shown hear of it', () => {
  it('says a paper filed, met again, or given a PDF for its figures, the same one or one setting the last aside', () => {
    expect(filedLine({ event: 'paper', existed: false, kept: false })).toBe('filed as new');
    expect(filedLine({ event: 'paper', existed: true, kept: true })).toBe('seen before'); // read already, kept as it was read
    expect(filedLine({ event: 'paper', existed: true, kept: true, figures_file: 'doi_1.figures.pdf', aside: null, same: false })).toBe('a PDF kept beside its XML for its figures: doi_1.figures.pdf');
    expect(filedLine({ event: 'paper', existed: true, kept: true, figures_file: 'doi_1.figures.pdf', aside: 'doi_1.figures.1.pdf', same: false })).toBe(
      'a PDF kept beside its XML for its figures: doi_1.figures.pdf; the one kept before set aside as doi_1.figures.1.pdf',
    );
    expect(filedLine({ event: 'paper', existed: true, kept: true, figures_file: 'doi_1.figures.pdf', aside: null, same: true })).toBe('the PDF kept for its figures, doi_1.figures.pdf, again: nothing new to read');
  });

  it('keeps the file a PDF kept beside the XML names, and what an event leaves unsaid', () => {
    const prev = { title: 'Threads', doi: '10.1/x', pages: 12, nodes: 40, has_methods: 1, seconds: 3, roles: { methods: 5 }, figures_file: 'doi_1.figures.pdf' };
    const kept = arrived('doi:1', { event: 'paper', paper: 'doi:1', existed: true, kept: true, figures_file: 'doi_1.figures.pdf', aside: 'doi_1.figures.1.pdf', file: 'doi_1.xml', status: 'parsed' }, { ...prev, figures_file: null });
    expect([kept.figures_file, kept.pages, kept.format, kept.title, kept.nodes, kept.roles]).toEqual(['doi_1.figures.pdf', 12, 'jats', 'Threads', 40, { methods: 5 }]);
    const met = arrived('doi:1', { event: 'paper', paper: 'doi:1', existed: true, kept: true, file: 'doi_1.xml', status: 'parsed', pages: null }, prev);
    expect([met.figures_file, met.pages]).toEqual(['doi_1.figures.pdf', 12]); // an event naming neither leaves both as they were
    const filed = arrived('doi:2', { event: 'paper', paper: 'doi:2', existed: false, file: 'doi_2.pdf', status: 'queued', pages: 9 });
    expect([filed.figures_file, filed.pages, filed.format, filed.title, filed.nodes]).toEqual([null, 9, 'pdf', 'doi_2.pdf', 0]);
    expect(arrived('doi:3', { event: 'paper', file: 'doi_3.xml', status: 'queued', pages: null }).pages).toBeNull();
  });

  it('draws the figure shown again after the figures op, or an ingest that read the shown paper’s figures', () => {
    expect(readFiguresOf({ event: 'done', op: 'figures' }, null)).toBe(true);
    expect(readFiguresOf({ event: 'done', op: 'ingest', parsed: [], figures: ['doi:1'] }, 'doi:1')).toBe(true);
    expect(readFiguresOf({ event: 'done', op: 'ingest', parsed: ['doi:1'], figures: [] }, 'doi:1')).toBe(false); // read as a paper, not given a PDF for its figures
    expect(readFiguresOf({ event: 'done', op: 'ingest', figures: ['doi:2'] }, 'doi:1')).toBe(false);
    expect(readFiguresOf({ event: 'done', op: 'ingest', figures: ['doi:1'] }, null)).toBe(false);
    expect(readFiguresOf({ event: 'done', op: 'ingest', parsed: [] }, 'doi:1')).toBe(false); // a worker from before the field
    expect(readFiguresOf({ event: 'done', op: 'fetch', figures: ['doi:1'] }, 'doi:1')).toBe(false);
  });
});
