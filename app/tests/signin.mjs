// Gets a headless run past the sign-in dialogue the window opens with. A run against a library
// that is somebody's signs in as an account of theirs, named by LITRAG_USER and LITRAG_PASSWORD,
// and never makes one: an account cannot be removed through the window. Only a run on a library
// it made itself makes an account there.

/** The account LITRAG_USER and LITRAG_PASSWORD name, or null when either is unset. */
export function ownAccount() {
  const name = process.env.LITRAG_USER;
  const password = process.env.LITRAG_PASSWORD;
  return name && password ? { name, password } : null;
}

/** Signs in as an account the library already has; throws, with the dialogue's own words, when it refuses. */
export async function signIn(page, { name, password }) {
  await page.waitForSelector('#signin:not([hidden])', { timeout: 30_000 });
  if (await page.isVisible('#signin-confirm-row')) await page.click('#signin-mode');
  await page.fill('#signin-name', name);
  await page.fill('#signin-password', password);
  await page.click('#signin-submit');
  const outcome = await Promise.race([
    page.waitForSelector('#signin', { state: 'hidden', timeout: 15_000 }).then(() => 'in'),
    page.waitForSelector('#signin-error:not([hidden])', { timeout: 15_000 }).then(() => 'no'),
  ]);
  if (outcome === 'no') throw new Error(`could not sign in as ${name}: ${(await page.textContent('#signin-error'))?.trim()}`);
}

/** Makes an account and signs into it: for a library the run made itself. */
export async function makeAccount(page, name, password) {
  await page.waitForSelector('#signin:not([hidden])', { timeout: 30_000 });
  if (await page.isHidden('#signin-confirm-row')) await page.click('#signin-mode');
  await page.fill('#signin-name', name);
  await page.fill('#signin-password', password);
  await page.fill('#signin-confirm', password);
  await page.click('#signin-submit');
  await page.waitForSelector('#signin', { state: 'hidden', timeout: 15_000 });
}
