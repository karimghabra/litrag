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
