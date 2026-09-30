// An installed litrag, launched as a person would start it: the packaged app in <root>/app,
// its parser in <root>/venv. Waits for the worker, asks it hello, takes a screenshot.
//   node tests/installed.mjs <install root> [out dir]
// The app finds venv/ by itself at the default root; elsewhere set LITRAG_VENV (or LITRAG_PARSER).
// LITRAG_ROOT picks the libraries, as for `npm start`. Needs a display (xvfb-run on Linux).
import { _electron as electron } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const root = resolve(process.argv[2] ?? '.');
const out = resolve(process.argv[3] ?? 'installed-out');
mkdirSync(out, { recursive: true });
const t0 = Date.now();
const say = (m) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s] ${m}`);

const app = await electron.launch({
  executablePath: join(root, 'app', process.platform === 'win32' ? 'litrag.exe' : 'litrag'),
  args: process.platform === 'linux' ? ['--no-sandbox'] : [],
  env: { ...process.env },
});
const page = await app.firstWindow();
page.on('pageerror', (e) => console.log('renderer exception:', e.message));
await page.waitForLoadState('domcontentloaded');
say(`window: ${await page.title()}, from ${await app.evaluate(({ app }) => app.getAppPath())}`);
await page.waitForSelector('#worker-status.ok, #worker-status.bad', { timeout: 120_000 });
const ok = (await page.$('#worker-status.ok')) !== null;
say(`worker: ${await page.textContent('#worker-status .text')}`);
if (ok) say(`hello: ${JSON.stringify(await page.evaluate(() => window.litrag.request('hello')))}`);
else say(`log pane:\n${await page.textContent('#log').catch(() => '?')}`);
await page.waitForTimeout(1000);
await page.screenshot({ path: join(out, ok ? 'installed.png' : 'installed-bad.png') });
await app.close();
say(`${ok ? 'ok' : 'FAIL'} → ${out}`);
process.exit(ok ? 0 : 1);
