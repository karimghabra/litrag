// A headless run of the real app: launches Electron, waits for the worker, optionally
// ingests a PDF, and takes screenshots. Needs a display (xvfb-run on Linux, or a desktop).
//   LITRAG_ROOT=/some/root SMOKE_PDF=/some/paper.pdf SMOKE_OUT=/some/dir node tests/smoke.mjs
import { _electron as electron } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const out = process.env.SMOKE_OUT ?? 'smoke-out';
mkdirSync(out, { recursive: true });
const t0 = Date.now();
const say = (m) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s] ${m}`);

const app = await electron.launch({ args: ['.', '--no-sandbox'], env: { ...process.env } });
const page = await app.firstWindow();
page.on('console', (m) => console.log(`renderer ${m.type()}:`, m.text().slice(0, 300)));
page.on('pageerror', (e) => console.log('renderer exception:', e.message));
await page.waitForLoadState('domcontentloaded');
say(`window: ${await page.title()}`);

await page.waitForSelector('#worker-status.ok, #worker-status.bad', { timeout: 60_000 });
say(`worker: ${await page.textContent('#worker-status .text')}`);
if (await page.$('#worker-status.bad')) { await page.screenshot({ path: join(out, 'worker-bad.png') }); await app.close(); process.exit(1); }

// a library to work in
page.on('console', (m) => { if (m.type() !== 'error') console.log('renderer:', m.text()); });
const fail = async (why) => {
  process.stderr.write(`FAIL: ${why}\n`);
  process.stderr.write('log pane:\n' + (await page.textContent('#log').catch(() => '?')) + '\n');
  process.stderr.write('library select: ' + (await page.$eval('#library', (s) => s.outerHTML).catch(() => '?')) + '\n');
  await page.screenshot({ path: join(out, 'fail.png') }).catch(() => {});
  await app.close().catch(() => {});
  process.exit(1);
};
const direct = await Promise.race([
  page.evaluate(() => window.litrag.request('libraries')),
  new Promise((r) => setTimeout(() => r('TIMEOUT'), 10_000)),
]);
say(`direct libraries request: ${JSON.stringify(direct).slice(0, 200)}`);
try {
  await page.waitForFunction(() => document.querySelectorAll('#library option').length > 0, null, { timeout: 20_000 });
} catch (e) {
  await fail(`no library option: ${e.message}`);
}
let lib = await page.$eval('#library', (s) => s.value);
if (!lib) {
  // through the button, as a person would: the window then reloads its libraries
  await page.evaluate(() => { window.prompt = () => 'Smoke Test'; });
  await page.click('#new-library');
  await page.waitForFunction(() => document.querySelector('#library').value !== '', null, { timeout: 20_000 }).catch(() => fail('library not created'));
  lib = await page.$eval('#library', (s) => s.value);
}
say(`library: ${lib}`);

if (process.env.SMOKE_PDF) {
  const before = await page.$$eval('#papers .paper', (n) => n.length);
  await page.evaluate(({ lib, path }) => window.litrag.request('ingest', { lib, paths: [path] }), { lib, path: process.env.SMOKE_PDF });
  await page.waitForFunction((n) => document.querySelectorAll('#papers .paper').length > n, before, { timeout: 30_000 }).catch(() => fail('paper never listed'));
  say('paper filed');
  await page.waitForSelector('#papers .paper .badge.parsing', { timeout: 60_000 }).catch(() => {});
  await page.screenshot({ path: join(out, '1-parsing.png') });
  await page.waitForFunction(() => document.querySelector('#papers .paper .badge.parsing') === null, null, { timeout: 300_000 }).catch(() => fail('still parsing'));
  say(`parsed: ${await page.$$eval('#papers .paper .badge', (b) => b.map((x) => x.textContent).join(','))}`);
}

await page.waitForSelector('#papers .paper .badge.parsed', { timeout: 30_000 }).catch(() => fail('no parsed paper'));
await page.click('#papers .paper .badge.parsed >> xpath=ancestor::div[contains(@class,"paper")]');
await page.waitForSelector('#tree .tree-node', { timeout: 30_000 }).catch(() => fail('tree never rendered'));
say(`tree: ${await page.textContent('#tree-summary')}`);
// open the methods section and select its first paragraph
const methods = await page.$('#tree .tree-node.section .role[title="methods"] >> xpath=..');
if (methods) {
  await methods.click();
  const para = await page.$('#tree .tree-node.paragraph .role[title="methods"] >> xpath=..');
  if (para) await para.click();
  await page.waitForFunction(() => document.querySelectorAll('#page-overlay .box').length > 0, null, { timeout: 30_000 }).catch(() => say('no boxes drawn (XML paper?)'));
}
say(`detail: ${(await page.textContent('#node-detail .crumbs'))?.trim()}`);
say(`boxes on page: ${await page.$$eval('#page-overlay .box', (b) => b.length)}`);
const ink = await page.evaluate(() => {
  const c = document.getElementById('page-canvas');
  if (!c.width) return 'no canvas';
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let dark = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i] < 128 && d[i + 3] > 0) dark++;
  return `${c.width}x${c.height}, dark pixels: ${dark}`;
});
say(`canvas: ${ink}`);
const isPdf = await page.$eval('#papers .paper.selected .key', (k) => /\bpdf\b/.test(k.textContent));
if (isPdf && !/dark pixels: [1-9]/.test(ink)) await fail('PDF page rendered nothing');
if (isPdf && (await page.$$eval('#page-overlay .box', (b) => b.length)) === 0) await fail('no boxes on a PDF page');
await page.screenshot({ path: join(out, '2-tree.png') });
say('log pane (errors):\n' + (await page.$$eval('#log .log-line.error, #log .log-line.failed', (ls) => ls.map((l) => l.textContent).join('\n'))));
await app.close();
say(`done → ${out}`);
