import { describe, expect, it } from 'vitest';
import { closeGuard, pageQuestion } from '../src/main/closing.ts';

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

describe('the question put to the page, when the page cannot answer', () => {
  const tick = () => new Promise((done) => setTimeout(done, 0));

  /** A window whose close asks its page; `sent` counts the questions that reached it. */
  function windowAsking() {
    const q = pageQuestion();
    const seen = { sent: 0, stopped: 0, closed: 0 };
    const guard = closeGuard(() => q.ask(() => (seen.sent += 1)));
    const pressClose = () => guard(() => (seen.stopped += 1), () => (seen.closed += 1));
    return { q, seen, pressClose };
  }

  it('takes the page’s own answer', async () => {
    const w = windowAsking();
    w.pressClose();
    w.q.answer(false); // Cancel
    await tick();
    expect(w.seen).toEqual({ sent: 1, stopped: 1, closed: 0 });
    w.pressClose();
    w.q.answer(true);
    await tick();
    expect(w.seen).toEqual({ sent: 2, stopped: 2, closed: 1 });
  });

  it('closes a window whose page went, hung or began loading again while the question waited', async () => {
    const w = windowAsking();
    w.pressClose();
    w.pressClose(); // pressed again while nothing answers: still held, not asked twice
    expect(w.seen).toEqual({ sent: 1, stopped: 2, closed: 0 });
    w.q.answer(true); // what the window says for a page that will not: may close
    await tick();
    expect(w.seen.closed).toBe(1);
  });

  it('does not take an answer that comes when no question is waiting for one to the next question', async () => {
    const w = windowAsking();
    w.q.answer(true); // a page reloading with nothing asked
    w.pressClose();
    await tick();
    expect(w.seen).toEqual({ sent: 1, stopped: 1, closed: 0 }); // still waiting on the page
    w.q.answer(false);
    await tick();
    expect(w.seen.closed).toBe(0);
  });
});
