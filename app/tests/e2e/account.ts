import type { Page } from '@playwright/test';

/**
 * The window opens on the sign-in dialogue. On a fresh root there is no account yet, so the
 * dialogue offers to make one; a second call with another name makes a second account (the
 * dialogue is switched to "New account" when it lists accounts already).
 */
export async function signIn(page: Page, name = 'E2E Reader', password = 'e2e-password'): Promise<void> {
  await page.locator('#signin').waitFor({ state: 'visible', timeout: 60_000 });
  if (await page.locator('#signin-confirm-row').isHidden()) await page.click('#signin-mode');
  await page.fill('#signin-name', name);
  await page.fill('#signin-password', password);
  await page.fill('#signin-confirm', password);
  await page.click('#signin-submit');
  await page.locator('#signin').waitFor({ state: 'hidden', timeout: 20_000 });
}
