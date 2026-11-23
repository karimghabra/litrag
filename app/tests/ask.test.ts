import { describe, expect, it } from 'vitest';
import { citationText, metaLine, modelChoices, orderedSources, passagesAsked, statusLine, weakReason, type AskAnswer, type AskSource } from '../src/renderer/ask';

const source = (n: number, extra: Partial<AskSource> = {}): AskSource => ({
  n, lib: 'collagen', node_id: `doi:10.1/x#p-${n}`, paper: 'doi:10.1/x', title: 'Thermal stability of fish collagen', year: '2022',
  ancestry: ['2. Results and Discussion', '2.4. Thermal Stability'], page: 4, text: `passage ${n}`, ...extra,
});

const answer = (extra: Partial<AskAnswer> = {}): AskAnswer => ({
  question: 'What is the denaturation temperature?', libs: ['collagen'], model: 'qwen3.5:9b', status: 'answered', summary: '',
  claims: [{ text: 'It denatures at 32.9 °C.', sources: [2], support: 'supported' }], dropped: [], missing: '',
  sources: [source(1), source(2, { cited: true }), source(3, { paper: 'doi:10.1/y' })], timing: { total: 12.34, tokens_per_second: 41.5 }, ...extra,
});

describe('the Ask tab', () => {
  it('says a citation as paper (year), section path and page', () => {
    expect(citationText(source(1))).toBe('Thermal stability of fish collagen (2022) — 2. Results and Discussion › 2.4. Thermal Stability — p. 4');
    expect(citationText(source(1, { title: null, year: null, ancestry: [], section: 'Methods', page: null }))).toBe('doi:10.1/x — Methods');
  });

  it('names every status, a refusal as not in the library', () => {
    expect(statusLine({ status: 'answered' })[1]).toBe('ok');
    expect(statusLine({ status: 'not_found' })[0]).toBe('Not in the library');
    expect(statusLine({ status: 'unanswered' })[1]).toBe('bad');
  });

  it('tells why a sentence is marked to be checked', () => {
    expect(weakReason({ text: 'x', sources: [1], support: 'supported' })).toBe('');
    expect(weakReason({ text: 'x', sources: [1], support: 'weak', numbers_missing: ['120'] })).toContain('120 is not printed');
    expect(weakReason({ text: 'x', sources: [1], support: 'weak', numbers_missing: [] })).toContain('most of its words');
  });

  it('puts the cited passages first and says what was left out', () => {
    expect(orderedSources(answer()).map((s) => s.n)).toEqual([2, 1, 3]);
    const meta = metaLine(answer({ claims: [{ text: 'x', sources: [1], support: 'weak' }], dropped: [{ text: 'y', reason: 'cites nothing' }] }));
    expect(meta).toEqual(['qwen3.5:9b', '12.3 s', '41.5 tokens/s', '3 passages from 2 papers', '1 sentence to check', '1 sentence left out: it cited no passage given']);
  });

  it('selects the project\'s default model unless the person chose another', () => {
    const held = { models: [{ name: 'gemma4:12b' }, { name: 'qwen3.5:9b' }] };
    expect(modelChoices({ ...held, default: 'qwen3.5:9b' }, '', false).selected).toBe('qwen3.5:9b');
    // the picker showed project A's default; project B's library.json names gemma4:12b
    expect(modelChoices({ ...held, default: 'gemma4:12b' }, 'qwen3.5:9b', false).selected).toBe('gemma4:12b');
    expect(modelChoices({ ...held, default: 'gemma4:12b' }, 'qwen3.5:9b', true).selected).toBe('qwen3.5:9b');
    expect(modelChoices({ ...held, default: 'gemma4:12b' }, 'gone:1b', true).selected).toBe('gemma4:12b');
  });

  it('knows a default named without a tag as the :latest Ollama lists', () => {
    const pulled = modelChoices({ default: 'gemma4', models: [{ name: 'gemma4:latest' }, { name: 'qwen3.5:9b' }] }, '', false);
    expect(pulled).toEqual({ names: ['gemma4:latest', 'qwen3.5:9b'], default: 'gemma4:latest', held: true, selected: 'gemma4:latest' });
    const missing = modelChoices({ default: 'gemma4', models: [{ name: 'gemma4:12b' }] }, '', false);
    expect(missing).toEqual({ names: ['gemma4', 'gemma4:12b'], default: 'gemma4', held: false, selected: 'gemma4' });
  });

  it('holds the passages asked for to 1..20, and to 8 only when the field says nothing', () => {
    expect([passagesAsked('12'), passagesAsked('0'), passagesAsked('-3'), passagesAsked('99'), passagesAsked(''), passagesAsked('abc')]).toEqual([12, 1, 1, 20, 8, 8]);
  });

  it('carries the warning an answer came with in its footing', () => {
    expect(metaLine(answer({ warning: 'The window was filled.' }))).toContain('The window was filled.');
    expect(metaLine(answer())).not.toContain('The window was filled.');
  });
});
