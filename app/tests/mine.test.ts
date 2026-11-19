import { afterEach, describe, expect, it } from 'vitest';
import { me, mine, session } from '../src/renderer/account';
import { homeOf, rowMeta, type MinePaper } from '../src/renderer/mine';
import { ctx } from '../src/renderer/shared';

const paper = (over: Partial<MinePaper> = {}): MinePaper => ({
  paper: 'doi:10.1/a', project: null, title: 'A paper', year: '2024', journal: 'Micromachines', format: 'jats', status: 'parsed', projects: [], ...over,
});

describe('where a paper of yours is opened', () => {
  afterEach(() => {
    ctx.lib = null;
  });

  it('in the project it was last seen in, while that project still holds it', () => {
    expect(homeOf(paper({ project: 'bone', projects: ['tendon', 'bone'] }))).toBe('bone');
  });

  it('else in the project open now when it holds the paper, else in the first that does', () => {
    ctx.lib = 'bone';
    expect(homeOf(paper({ project: 'gone', projects: ['tendon', 'bone'] }))).toBe('bone');
    ctx.lib = 'other';
    expect(homeOf(paper({ project: 'gone', projects: ['tendon', 'bone'] }))).toBe('tendon');
  });

  it('a paper no project holds now keeps the project it was seen in, which the store may still answer for', () => {
    expect(homeOf(paper({ project: 'old-shape', projects: [] }))).toBe('old-shape');
    expect(homeOf(paper({ project: null, projects: [] }))).toBeNull();
  });
});

describe('what a row says of a paper of yours', () => {
  it('its year, its journal and the projects holding it, by name', () => {
    const names = (id: string) => ({ tendon: 'Tendon', bone: 'Bone' })[id] ?? id;
    expect(rowMeta(paper({ projects: ['tendon', 'bone'] }), names)).toBe('2024 · Micromachines · Tendon, Bone');
  });

  it('says so when no project holds it any more, and leaves out what it does not know', () => {
    expect(rowMeta(paper({ year: null, journal: null }))).toBe('in no project now');
  });
});

describe('the accounts, as the worker keeps them', () => {
  it('asks nothing of anyone\'s own while no one is signed in', async () => {
    expect(me()).toBeNull();
    expect(session()).toBeUndefined();
    await expect(mine('recent')).rejects.toThrow('not signed in');
  });
});
