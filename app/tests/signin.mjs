// Gets a headless run past the sign-in dialogue the window opens with: signs in as `name`, or
// makes that account when there is none by that name. LITRAG_USER and LITRAG_PASSWORD name an
// account of your own instead; otherwise the run uses (and on first use makes) a throwaway one.
export async function signInOrMake(page, name, password) {
  name = process.env.LITRAG_USER || name;
  password = process.env.LITRAG_PASSWORD || password;
  await page.waitForSelector('#signin:not([hidden])', { timeout: 30_000 });
  const making = async () => {
    if (await page.isHidden('#signin-confirm-row')) await page.click('#signin-mode');
    await page.fill('#signin-name', name);
    await page.fill('#signin-password', password);
    await page.fill('#signin-confirm', password);
    await page.click('#signin-submit');
  };
  if (await page.isVisible('#signin-confirm-row')) {
    await making();
  } else {
    await page.fill('#signin-name', name);
    await page.fill('#signin-password', password);
    await page.click('#signin-submit');
    const outcome = await Promise.race([
      page.waitForSelector('#signin', { state: 'hidden', timeout: 15_000 }).then(() => 'in'),
      page.waitForSelector('#signin-error:not([hidden])', { timeout: 15_000 }).then(() => 'no'),
    ]);
    if (outcome === 'no') await making();
  }
  await page.waitForSelector('#signin', { state: 'hidden', timeout: 15_000 });
}
