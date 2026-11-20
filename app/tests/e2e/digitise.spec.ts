/**
 * The Plots tab, end to end: the real window and worker over a library whose one paper's one
 * figure is a synthetic turbidity plot of known data (parser/tests/fixtures/digitise). A person
 * finds it by its caption, opens it, confirms the axes the worker read from its numbers, picks a
 * series by one of the figure's colours, finds its points, saves them, fits a logistic curve and
 * exports the CSV with its sidecar; then digitises an image file the same way. The save and open
 * dialogs are answered in the main process. No models are needed: the library is made by the
 * parser's own store code, not by Docling.
 */
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const APP_DIR = resolve(import.meta.dirname, '..', '..');
const PARSER = resolve(APP_DIR, '..', 'parser');
const FIXTURES = join(PARSER, 'tests', 'fixtures', 'digitise');
const TRUTH = JSON.parse(readFileSync(join(FIXTURES, 'truth.json'), 'utf8')) as Record<string, { series: { name: string; colour: string }[] }>;

// a library with one PDF paper whose one picture is the fixture's page, captioned as a turbidity assay
const MAKE = `
import shutil, sys
from pathlib import Path
import pypdfium2 as pdfium
from litrag_parser.library import create_library
from litrag_parser.store import file_paper, open_store
root, src = Path(sys.argv[1]), Path(sys.argv[2])
lib = create_library(root, "Plots")
lib.ensure_dirs()
shutil.copy(src, lib.papers_dir / "paper.pdf")
conn = open_store(lib.store_path)
key = file_paper(conn, title="Collagen fibrillogenesis kinetics", file="paper.pdf", sha256="e2e", fmt="pdf", doi="10.1000/plots", pmid=None, pmcid=None, now="t").key
pdf = pdfium.PdfDocument(str(lib.papers_dir / "paper.pdf")); w, h = pdf[0].get_size(); pdf.close()
fig = key + "#section-1#picture-1"
rows = [(key + "#section-1", key, None, 0, 1, "section", "section_header", "results", "3 Results", "[]", "", None, None, None, None, None),
        (fig, key, key + "#section-1", 1, 2, "picture", "picture", "results", None, "[]", "", 1, 0.0, 0.0, w, h),
        (fig + "#caption-1", key, fig, 0, 3, "caption", "caption", "results", None, "[]", "Figure 1. Turbidity of collagen solutions over time at 37 C.", 1, 0.0, h - 10, w, h)]
conn.executemany("INSERT INTO nodes(node_id, paper, parent, ordinal, depth, type, label, role, heading, ancestry, text, page, bbox_l, bbox_t, bbox_r, bbox_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
conn.execute("UPDATE papers SET status = 'parsed' WHERE key = ?", (key,))
conn.commit()
print(lib.id)
`;

let app: ElectronApplication;
let page: Page;
let root: string;
let out: string;

const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const dist = (a: number[], b: number[]) => Math.hypot(a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!);

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'litrag-plots-'));
  out = mkdtempSync(join(tmpdir(), 'litrag-plots-out-'));
  execFileSync('uv', ['run', '--project', PARSER, '--no-sync', 'python', '-c', MAKE, root, join(FIXTURES, 'turbidity_markers.pdf')], { stdio: 'inherit' });
  app = await electron.launch({ args: [APP_DIR, '--no-sandbox'], env: { ...process.env, LITRAG_ROOT: root } });
  page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
  await page.waitForSelector('#worker-status.ok', { timeout: 60_000 });
  await app.evaluate(({ dialog }, files) => {
    dialog.showSaveDialog = (async () => ({ canceled: false, filePath: files.csv })) as unknown as typeof dialog.showSaveDialog;
    dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [files.image] })) as unknown as typeof dialog.showOpenDialog;
  }, { csv: join(out, 'turbidity.csv'), image: join(FIXTURES, 'noisy_scan.jpg') });
});

test.afterAll(async () => {
  await app?.close();
});

async function pickSwatch(colour: string): Promise<void> {
  const swatches = page.locator('#dg-palette button');
  await expect(swatches.first()).toBeVisible();
  const titles = await swatches.evaluateAll((bs) => bs.map((b) => (b as HTMLElement).title.slice(0, 7)));
  const best = titles.map((t, i) => [dist(rgb(t), rgb(colour)), i] as const).sort((a, b) => a[0] - b[0])[0]!;
  expect(best[0]).toBeLessThan(45);
  await swatches.nth(best[1]).click();
}

test('a figure found by its caption is digitised, saved, fitted and exported traceably', async () => {
  await page.selectOption('#library', 'plots');
  await page.click('#nav button[data-view="plots"]');
  await page.fill('#plots-q', 'turbidity');
  await page.click('#plots-form button[type="submit"]');
  const card = page.locator('#plots-list .plot-card').first();
  await expect(card).toContainText('Figure 1');
  await card.click();
  await expect(page.locator('#dg-canvas-wrap')).toBeVisible({ timeout: 60_000 });
  // the axes the worker read, offered: four points set, to be confirmed
  await expect(page.locator('#dg-auto')).toContainText('read from the figure', { timeout: 30_000 });
  await expect(page.locator('.dg-val[data-val="x2"]')).toHaveValue('60');
  await expect(page.locator('#dg-xunit')).toHaveValue('min');
  await page.click('#dg-cal-state button');
  await expect(page.locator('#dg-cal-state')).toHaveText('axes confirmed');
  const series = TRUTH['turbidity_markers']!.series[0]!;
  await pickSwatch(series.colour);
  await page.click('#dg-extract');
  await expect(page.locator('#dg-count')).toContainText(/1\d points · not saved/);
  // a point added by hand, then deleted again
  await page.click('#dg-tools [data-tool="add"]');
  const box = (await page.locator('#dg-canvas').boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.2);
  const n = Number((await page.locator('#dg-count').textContent())!.match(/\d+/)![0]);
  await page.click('#dg-tools [data-tool="delete"]');
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.2);
  await expect(page.locator('#dg-count')).toContainText(`${n - 1} points`);
  await page.fill('#dg-name', series.name);
  await page.click('#dg-save');
  await expect(page.locator('#dg-saved .saved')).toContainText(series.name);
  await page.click('#dg-fit');
  await expect(page.locator('#dg-fit-out table')).toContainText('Time to half maximum');
  await expect(page.locator('#dg-fit-out svg')).toBeVisible();
  await page.screenshot({ path: join(out, 'plots-figure.png') });
  await page.click('#dg-export');
  await expect.poll(() => existsSync(join(out, 'turbidity.csv.json')), { timeout: 20_000 }).toBe(true);
  const csv = readFileSync(join(out, 'turbidity.csv'), 'utf8').trim().split('\n');
  expect(csv[0]).toMatch(/^Time \(min\),/);
  expect(csv.length).toBe(n); // the header and every point kept
  const side = JSON.parse(readFileSync(join(out, 'turbidity.csv.json'), 'utf8'));
  expect(side.paper.doi).toBe('10.1000/plots');
  expect(side.series[0].fits.logistic.status).toBe('fit');
  expect(side.series[0].points[0]).toHaveProperty('px');
  expect(await page.locator('#log .log-line.error').count()).toBe(0);
});

test('an image file is digitised and exported without a library row', async () => {
  await page.click('#dg-open-image');
  await expect(page.locator('#dg-title')).toContainText('noisy_scan.jpg', { timeout: 60_000 });
  await expect(page.locator('#dg-auto')).toContainText('read from the figure', { timeout: 30_000 });
  await page.click('#dg-cal-state button');
  await pickSwatch(TRUTH['noisy_scan']!.series[0]!.colour);
  await page.click('#dg-extract');
  await expect(page.locator('#dg-count')).toContainText(/1\d points/);
  await page.click('#dg-fit');
  await expect(page.locator('#dg-fit-out table')).toContainText('Lag time');
  await page.screenshot({ path: join(out, 'plots-image.png') });
  await page.click('#dg-export');
  await expect.poll(() => JSON.parse(readFileSync(join(out, 'turbidity.csv.json'), 'utf8')).figure.file ?? null, { timeout: 20_000 }).toContain('noisy_scan.jpg');
  expect(await page.locator('#log .log-line.error').count()).toBe(0);
  console.log(`screenshots in ${out}`);
});
