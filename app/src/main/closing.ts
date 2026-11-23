/**
 * A window's close, held until what is not saved in it has been asked about. The window asks
 * the page (`ask`: the Plots tab's Save, Discard or Cancel, true when the window may go); the
 * close that prompted the question is stopped, and when the answer is yes the window is closed
 * for good. One question at a time, however often the close button is pressed meanwhile, and a
 * close called off is asked about afresh the next time.
 */
export function closeGuard(ask: () => Promise<boolean>): (prevent: () => void, close: () => void) => void {
  let allowed = false;
  let asking = false;
  return (prevent, close) => {
    if (allowed) return;
    prevent();
    if (asking) return;
    asking = true;
    void ask()
      .catch(() => true) // a page that cannot answer has nothing to ask about
      .then((ok) => {
        asking = false;
        if (!ok) return;
        allowed = true;
        close();
      });
  };
}

/**
 * The question a window puts to its page, and the one answer it takes: `ask` sends it and waits;
 * `answer` settles the question that is waiting, and does nothing when none is. The page's own
 * answer comes through it, and so does everything that means the page will never give one — it
 * is gone, it hangs, it is loading again — as "may close": a window whose page cannot answer is
 * never one that cannot be closed.
 */
export function pageQuestion(): { ask(send: () => void): Promise<boolean>; answer(ok: boolean): void } {
  let waiting: ((ok: boolean) => void) | null = null;
  return {
    ask(send) {
      return new Promise<boolean>((resolve) => {
        waiting?.(true);
        waiting = resolve;
        send();
      });
    },
    answer(ok) {
      const settle = waiting;
      waiting = null;
      settle?.(ok);
    },
  };
}
