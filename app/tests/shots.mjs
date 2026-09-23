// Screenshots of every tab of the real window, for a person (or an assistant) to look at:
//   node tests/shots.mjs <out dir> [project id]
// LITRAG_ROOT picks the libraries, as for `npm start`. The app is built first (`node build.mjs`).
import { _electron as electron } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const out = resolve(process.argv[2] ?? 'shots');
const project = process.argv[3];
mkdirSync(out, { recursive: true });
const app = await electron.launch({ args: [resolve(import.meta.dirname, '..'), '--no-sandbox'], env: { ...process.env } });
const page = await app.firstWindow();
await page.setViewportSize({ width: 1600, height: 1000 }).catch(() => {});
await page.waitForSelector('#worker-status.ok', { timeout: 60_000 });
await page.waitForTimeout(1500);
if (project) await page.selectOption('#library', project);
const shot = async (name) => {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: join(out, `${name}.png`) });
  console.log(join(out, `${name}.png`));
};
for (const view of ['projects', 'search', 'papers', 'types', 'query']) {
  await page.click(`#nav button[data-view="${view}"]`);
  if (view === 'papers') {
    await page.locator('#papers .paper').first().click().catch(() => {});
    await page.waitForSelector('#page-overlay .box, #page-reading:not([hidden])', { timeout: 20_000 }).catch(() => console.log('no page drawn within 20 s'));
    await page.locator('#tree .tree-node.paragraph').nth(3).click().catch(() => {});
    await page.waitForTimeout(1500);
    await shot('papers-printed');
    await page.click('#tree-mode button[data-mode="canonical"]').catch(() => {});
    await page.waitForTimeout(1500);
    await shot('papers-canonical');
    await page.click('#tree-mode button[data-mode="printed"]').catch(() => {});
    continue;
  }
  if (view === 'types') await page.waitForTimeout(2500);
  if (view === 'query' && process.env['SHOT_QUESTION']) {
    await page.fill('#query-q', process.env['SHOT_QUESTION']);
    await page.click('#query-form button[type="submit"]');
    await page.waitForSelector('#query-results .qhit', { timeout: 60_000 }).catch(() => {});
  }
  await shot(view);
}
const errors = await page.locator('#log .log-line.error').allTextContents();
console.log(JSON.stringify({ errors }, null, 1));
await app.close();
