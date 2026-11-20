import { describe, expect, it } from 'vitest';
import { delegated } from '../src/digitise.ts';

describe('lit digitise and lit fit hand their arguments to the parser whole', () => {
  it('passes everything after the verb, flags the CLI would misread included', () => {
    expect(delegated(['digitise', 'plot.png', '--xlog', '--colour', '#1f4e9c', '--fit', 'both'])).toEqual(['digitise', 'plot.png', '--xlog', '--colour', '#1f4e9c', '--fit', 'both']);
    expect(delegated(['fit', 'points.csv', '--model', 'gompertz'])).toEqual(['fit', 'points.csv', '--model', 'gompertz']);
  });

  it('carries --root and --json given before the verb, and reads digitize as digitise', () => {
    expect(delegated(['--root', '/libs', '--json', 'digitize', 'fig#1', '--lib', 'a'])).toEqual(['digitise', 'fig#1', '--lib', 'a', '--root', '/libs', '--json']);
    expect(delegated(['--json', 'fit', 'p.csv', '--json'])).toEqual(['fit', 'p.csv', '--json']);
  });

  it('leaves every other verb to the CLI', () => {
    expect(delegated(['query', 'lib', 'question'])).toBeNull();
    expect(delegated(['--root', '/x', 'papers', 'lib'])).toBeNull();
    expect(delegated([])).toBeNull();
  });
});
