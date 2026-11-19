/**
 * A question with a line of text for its answer, asked inside the window: Electron gives a page
 * no `window.prompt` (it throws "prompt() is not supported"), so every rename, every new
 * collection's name and every password change asks through this instead.
 */

import { $ } from './shared.ts';

export interface AskOptions {
  /** what the box holds to begin with */
  value?: string;
  /** a password: typed into a box that hides it */
  secret?: boolean;
  /** the button that accepts (OK by default) */
  ok?: string;
  /** a textarea instead of one line, for a note */
  long?: boolean;
}

let pending: ((answer: string | null) => void) | null = null;

function close(answer: string | null): void {
  $('ask').hidden = true;
  const done = pending;
  pending = null;
  done?.(answer);
}

/** Asks `question`; resolves with what was typed, or null when cancelled. */
export function askText(question: string, opts: AskOptions = {}): Promise<string | null> {
  if (pending) close(null);
  $('ask-question').textContent = question;
  const line = $<HTMLInputElement>('ask-line');
  const long = $<HTMLTextAreaElement>('ask-long');
  line.hidden = Boolean(opts.long);
  long.hidden = !opts.long;
  line.type = opts.secret ? 'password' : 'text';
  const box = opts.long ? long : line;
  box.value = opts.value ?? '';
  $('ask-ok').textContent = opts.ok ?? 'OK';
  $('ask').hidden = false;
  box.focus();
  box.select();
  return new Promise((resolve) => (pending = resolve));
}

export function initDialog(): void {
  $<HTMLFormElement>('ask-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const long = !$('ask-long').hidden;
    close(long ? $<HTMLTextAreaElement>('ask-long').value : $<HTMLInputElement>('ask-line').value);
  });
  $('ask-cancel').addEventListener('click', () => close(null));
  $('ask').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close(null);
  });
}
