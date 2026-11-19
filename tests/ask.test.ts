/**
 * `lit ask` against a stand-in worker: the request it sends on the wire, the progress it hears,
 * the answer it hands back, and a worker that fails or dies said as such.
 */

import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { askRequest, askWorker, formatAnswer, type AskAnswer } from '../src/ask.ts';

const dir = mkdtempSync(join(tmpdir(), 'lit-ask-'));
afterAll(() => rmSync(dir, { recursive: true, force: true }));

/** A worker that says ready, answers one `ask` the way `mode` says, and quits on `quit`. */
function standIn(mode: 'answer' | 'error' | 'die'): { cmd: string; args: string[] } {
  const script = join(dir, `${mode}.mjs`);
  writeFileSync(script, `
import { createInterface } from 'node:readline';
const say = (e) => process.stdout.write(JSON.stringify(e) + '\\n');
say({ event: 'ready', root: process.argv.find((a) => a.startsWith('--root='))?.slice(7) });
for await (const line of createInterface({ input: process.stdin })) {
  const req = JSON.parse(line);
  if (req.op === 'quit') { say({ event: 'bye' }); process.exit(0); }
  if (${JSON.stringify(mode)} === 'die') process.exit(3);
  say({ event: 'asking', id: req.id, stage: 'model', message: 'Asking stand-in' });
  if (${JSON.stringify(mode)} === 'error') { say({ event: 'error', id: req.id, message: 'No library "nope"' }); continue; }
  say({ event: 'answer', id: req.id, question: req.question, libs: req.libs, model: req.model ?? 'default', status: 'answered',
        summary: 'It melts at 27.25 °C [1].', claims: [{ text: 'It melts at 27.25 °C.', sources: [1], support: 'supported' }], dropped: [], missing: '',
        sources: [{ n: 1, lib: req.libs[0], node_id: 'doi:10.1/x#p1', paper: 'doi:10.1/x', title: 'A gel', year: '2026', ancestry: ['3 Results'], page: 5, text: 't', cited: true }],
        timing: { total: 3.2 }, k: req.k });
}
`);
  return { cmd: process.execPath, args: [script] };
}

describe('lit ask', () => {
  it('sends one ask on the wire and hands back the answer, hearing the progress', async () => {
    const heard: string[] = [];
    const a = await askWorker(standIn('answer'), '/libraries', { libs: ['gels', 'more'], question: 'When does it melt?', model: 'm', k: 5 }, (ev) => heard.push(String(ev['message'])));
    expect(a.status).toBe('answered');
    expect(a.libs).toEqual(['gels', 'more']);
    expect(a.model).toBe('m');
    expect((a as AskAnswer & { k: number }).k).toBe(5);
    expect(heard).toEqual(['Asking stand-in']);
    const text = formatAnswer(a);
    expect(text).toContain('It melts at 27.25 °C [1].');
    expect(text).toContain('*[1] A gel (2026) — 3 Results — p. 5  [gels]');
  });

  it('says a worker error, and a worker that died, as errors', async () => {
    await expect(askWorker(standIn('error'), '/x', { libs: ['nope'], question: 'q' })).rejects.toThrow('No library "nope"');
    await expect(askWorker(standIn('die'), '/x', { libs: ['a'], question: 'q' })).rejects.toThrow(/ended \(exit 3\) without an answer/);
    await expect(askWorker({ cmd: join(dir, 'no-such-program'), args: [] }, '/x', { libs: ['a'], question: 'q' })).rejects.toThrow(/would not start/);
  });

  it('leaves out what was not given', () => {
    expect(askRequest('a1', { libs: ['x'], question: 'q' })).toEqual({ id: 'a1', op: 'ask', libs: ['x'], question: 'q' });
  });

  it('marks what to check and what was left out for a person', () => {
    const text = formatAnswer({
      question: 'q', libs: ['a'], model: 'm', status: 'partial', summary: 'S.', missing: 'the dose', sources: [],
      claims: [{ text: 'It was 9 °C.', sources: [2], support: 'weak', numbers_missing: ['9'] }], dropped: [{ text: 'z', reason: 'cites nothing' }],
    });
    expect(text).toContain('Not in the passages: the dose');
    expect(text).toContain('  - It was 9 °C. [2] · not in the passage: 9');
    expect(text).toContain('1 sentence left out');
  });
});
