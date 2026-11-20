import { describe, expect, it } from 'vitest';
import { closeGuard } from '../src/main/closing.ts';

/** A window as the guard sees one: closes asked for, stopped, and let through. */
function windowWith(answers: (boolean | Error)[]) {
  const seen = { asked: 0, stopped: 0, closed: 0, went: 0 };
  let release: (() => void)[] = [];
  const ask = () =>
    new Promise<boolean>((resolve, reject) => {
      const a = answers[seen.asked]!;
      seen.asked += 1;
      release.push(() => (a instanceof Error ? reject(a) : resolve(a)));
    });
  const guard = closeGuard(ask);
  const pressClose = () => {
    let stopped = false;
    guard(
      () => {
        stopped = true;
        seen.stopped += 1;
      },
      () => {
        seen.closed += 1;
        pressClose(); // closing for good raises the close again: it must go through
      },
    );
    if (!stopped) seen.went += 1;
  };
  const answer = async () => {
    const r = release;
    release = [];
    r.forEach((f) => f());
    await new Promise((done) => setTimeout(done, 0));
  };
  return { seen, pressClose, answer };
}

describe('closing the window with work that is not saved', () => {
  it('holds the close until the question is answered, and closes when the answer is yes', async () => {
    const w = windowWith([true]);
    w.pressClose();
    expect(w.seen).toEqual({ asked: 1, stopped: 1, closed: 0, went: 0 });
    await w.answer();
    expect(w.seen).toEqual({ asked: 1, stopped: 1, closed: 1, went: 1 }); // closed once, not asked again on the way out
  });

  it('keeps the window open on Cancel, and asks again the next time', async () => {
    const w = windowWith([false, true]);
    w.pressClose();
    await w.answer();
    expect(w.seen).toEqual({ asked: 1, stopped: 1, closed: 0, went: 0 });
    w.pressClose();
    expect(w.seen.asked).toBe(2);
    await w.answer();
    expect(w.seen.closed).toBe(1);
    expect(w.seen.went).toBe(1);
  });

  it('asks once, however often close is pressed before the answer', async () => {
    const w = windowWith([true]);
    w.pressClose();
    w.pressClose();
    w.pressClose();
    expect(w.seen).toEqual({ asked: 1, stopped: 3, closed: 0, went: 0 });
    await w.answer();
    expect(w.seen.closed).toBe(1);
  });

  it('lets a window go whose page cannot answer', async () => {
    const w = windowWith([new Error('the page is gone')]);
    w.pressClose();
    await w.answer();
    expect(w.seen.closed).toBe(1);
    expect(w.seen.went).toBe(1);
  });
});
