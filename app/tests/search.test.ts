import { describe, expect, it } from 'vitest';
import { fetchable, heldElsewhere, statusLabel, type Candidate } from '../src/renderer/search';
import { leftOutLine } from '../src/renderer/query';

const cand = (over: Partial<Candidate>): Candidate => ({ cand_id: 1, status: 'found', ...over }) as Candidate;

describe('a search hit read for another project only', () => {
  it('can be taken into this project, and says where it was read', () => {
    const c = cand({ status: 'ingested', held_here: false });
    expect(heldElsewhere(c)).toBe(true);
    expect(fetchable(c)).toBe(true);
    expect(statusLabel(c)).toBe('read in another project');
  });

  it('one this project holds stays in its library, not to fetch again', () => {
    const c = cand({ status: 'ingested', held_here: true });
    expect(fetchable(c)).toBe(false);
    expect(statusLabel(c)).toBe('in library');
  });

  it('a library that keeps its own store sends no held_here: an ingested hit is its own', () => {
    expect(fetchable(cand({ status: 'ingested' }))).toBe(false);
    expect(fetchable(cand({ status: 'needs-pdf' }))).toBe(true);
  });
});

describe('what "All projects" says of the projects it could not ask', () => {
  it('names them, and how to bring them in', () => {
    expect(leftOutLine(['archive'])).toBe('Not asked: archive keeps its own store; move it into the shared store (Projects tab) to ask it with the rest.');
    expect(leftOutLine(['a', 'b'])).toContain('a, b keep their own store');
  });

  it('says nothing when every project was asked', () => {
    expect(leftOutLine([])).toBeNull();
    expect(leftOutLine(undefined)).toBeNull();
  });
});
