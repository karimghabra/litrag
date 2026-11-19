/**
 * Accounts: the sign-in dialogue the window opens with, the user menu in the header, and the
 * session every request about one person's own things carries (recent papers, favourites, notes,
 * collections, the projects they made).
 *
 * The accounts are the local worker's, asked for over its JSON-lines wire: it keeps them in
 * `users.sqlite` beside the library.
 *
 * What an account is for, said in the dialogue too: telling the people who share a machine (or a
 * library folder) apart and keeping each one's own things apart. It is not a lock: anyone who
 * can read the library folder can read what is kept there.
 */

import { askText } from './dialog.ts';
import { $, el, log, rejectionText, request } from './shared.ts';

export interface User {
  id: string;
  name: string;
}

interface SignedIn {
  user: User;
  session: string;
}

const state = {
  user: null as User | null,
  session: null as string | null,
  creating: false,
};

const listeners: ((user: User | null) => void)[] = [];

/** Called whenever someone signs in, or out (with null). */
export function onAccountChange(fn: (user: User | null) => void): void {
  listeners.push(fn);
}

export function me(): User | null {
  return state.user;
}

/** The session to send with a request that may make or change something of this person's own (`init`, `describe`), or nothing when signed out. */
export function session(): string | undefined {
  return state.session ?? undefined;
}

/** One of the per-person ops, as whoever is signed in. */
export async function mine<T = Record<string, unknown>>(op: string, params: Record<string, unknown> = {}): Promise<T> {
  if (!state.session) throw new Error('not signed in');
  return request<T>(op, { ...params, session: state.session });
}

function setUser(got: SignedIn | null): void {
  state.user = got?.user ?? null;
  state.session = got?.session ?? null;
  $('user-button').textContent = state.user ? state.user.name : 'Sign in';
  $('user-menu').hidden = !state.user;
  for (const fn of listeners) {
    try {
      fn(state.user);
    } catch (e) {
      log('error', `account: ${(e as Error).message}`);
    }
  }
}

function setMode(creating: boolean): void {
  state.creating = creating;
  $('signin-title').textContent = creating ? 'New account' : 'Sign in';
  $('signin-submit').textContent = creating ? 'Create account' : 'Sign in';
  $('signin-mode').textContent = creating ? 'I have an account' : 'New account…';
  $('signin-confirm-row').hidden = !creating;
  $<HTMLInputElement>('signin-password').autocomplete = creating ? 'new-password' : 'current-password';
  $('signin-users').hidden = creating || !$('signin-users').childElementCount;
  sayError('');
}

function sayError(text: string): void {
  const e = $('signin-error');
  e.textContent = text;
  e.hidden = !text;
}

/** Opens the dialogue: the accounts kept here to pick from, else a new one to make. */
export async function showSignIn(): Promise<void> {
  const box = $('signin-users');
  box.innerHTML = '';
  let users: User[] = [];
  try {
    users = (await request<{ users: User[] }>('users')).users;
  } catch (e) {
    log('error', `accounts: ${rejectionText(e)}`);
  }
  for (const u of users) {
    const b = el('button', 'ghost small', u.name) as HTMLButtonElement;
    b.type = 'button';
    b.addEventListener('click', () => {
      $<HTMLInputElement>('signin-name').value = u.name;
      $<HTMLInputElement>('signin-password').focus();
    });
    box.append(b);
  }
  $<HTMLInputElement>('signin-password').value = '';
  $<HTMLInputElement>('signin-confirm').value = '';
  setMode(users.length === 0);
  let last = '';
  try {
    last = window.localStorage.getItem('litrag.user') ?? '';
  } catch {
    // a convenience only
  }
  const name = $<HTMLInputElement>('signin-name');
  name.value = users.some((u) => u.name === last) ? last : '';
  $('signin').hidden = false;
  (name.value ? $<HTMLInputElement>('signin-password') : name).focus();
}

async function submit(): Promise<void> {
  const name = $<HTMLInputElement>('signin-name').value.trim();
  const password = $<HTMLInputElement>('signin-password').value;
  if (!name || !password) return sayError('A name and a password, please.');
  if (state.creating && password !== $<HTMLInputElement>('signin-confirm').value) return sayError('The two passwords are not the same.');
  const button = $<HTMLButtonElement>('signin-submit');
  button.disabled = true;
  try {
    const got = await request<SignedIn>(state.creating ? 'user_create' : 'sign_in', { name, password });
    $('signin').hidden = true;
    $<HTMLInputElement>('signin-password').value = '';
    $<HTMLInputElement>('signin-confirm').value = '';
    try {
      window.localStorage.setItem('litrag.user', got.user.name);
    } catch {
      // a convenience only
    }
    log('stage', `${state.creating ? 'Made the account of' : 'Signed in as'} ${got.user.name}`);
    setUser(got);
  } catch (e) {
    sayError(rejectionText(e));
  } finally {
    button.disabled = false;
  }
}

async function signOut(): Promise<void> {
  const s = state.session;
  setUser(null);
  if (s) await request('sign_out', { session: s }).catch(() => undefined);
  await showSignIn();
}

/** A new password, asked for twice as the sign-in form asks: null when cancelled, refused when the two differ. */
export async function askNewPassword(ask: typeof askText = askText): Promise<string | null> {
  const next = await ask('A new password (8 characters or more)', { secret: true, ok: 'Next' });
  if (next === null) return null;
  const again = await ask('The new password again', { secret: true, ok: 'Change it' });
  if (again === null) return null;
  if (again !== next) throw new Error('The two passwords are not the same.');
  return next;
}

async function changePassword(): Promise<void> {
  if (!state.session) return;
  const old = await askText('Your current password', { secret: true });
  if (old === null) return;
  try {
    const next = await askNewPassword();
    if (next === null) return;
    await request('password', { session: state.session, old, new: next });
    log('stage', 'Password changed');
  } catch (e) {
    window.alert(rejectionText(e));
  }
}

/** Wires the dialogue and the menu, and opens the dialogue: nothing of anyone's own is shown until someone signs in. */
export function initAccount(): void {
  $<HTMLFormElement>('signin-form').addEventListener('submit', (e) => {
    e.preventDefault();
    void submit();
  });
  $('signin-mode').addEventListener('click', () => setMode(!state.creating));
  const pop = $('user-pop');
  $('user-button').addEventListener('click', (e) => {
    e.stopPropagation();
    pop.hidden = !pop.hidden;
  });
  document.addEventListener('click', () => (pop.hidden = true));
  for (const b of pop.querySelectorAll<HTMLButtonElement>('button')) {
    b.addEventListener('click', () => {
      pop.hidden = true;
      const act = b.dataset['act'];
      if (act === 'signout') void signOut();
      if (act === 'password') void changePassword();
    });
  }
  setUser(null);
}
