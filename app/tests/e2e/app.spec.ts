/**
 * The app, end to end: launch the window, spawn the worker, make a library, ingest real
 * papers, and check what a person checks — every paper parsed, titled, with its methods
 * found; the tree free of one-letter nodes; the front matter a few typed nodes, not a
 * pile; citations linked; the page drawn (PDF) or the paper laid out (XML).
 *
 * With no environment the papers are the repository's JATS fixture — no models, no GPU,
 * a few seconds. `LITRAG_E2E_PAPERS=<dir>` runs every PDF and XML in that folder instead,
 * which needs Docling's models and takes minutes; `LITRAG_E2E_MIN_METHODS=0.7` and
 * `LITRAG_E2E_MIN_TITLES=0.9` are the corpus thresholds (reviews have no methods; a
 * folder of them wants the first lowered).
 */
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtempSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const APP_DIR = resolve(import.meta.dirname, '..', '..');
const FIXTURE = resolve(APP_DIR, '..', 'parser', 'tests', 'fixtures', 'PMC11278924.xml');
const GENERIC = new Set(['original research', 'original article', 'research article', 'article', 'review', 'review article', 'abstract', 'introduction', 'letter', 'communication', 'full paper', 'full length article', 'short communication', 'editorial', 'paper', 'research paper', 'original paper', 'major review', 'topical review', 'hhs public access', 'supporting information']);

function papersToTest(): string[] {
  const dir = process.env['LITRAG_E2E_PAPERS'];
  if (!dir) return [FIXTURE];
  return readdirSync(dir)
    .filter((f) => /\.(pdf|xml)$/i.test(f))
    .map((f) => join(dir, f))
    .filter((f) => statSync(f).isFile())
    .sort();
}

interface PaperRow {
  key: string;
  title: string;
  format: string | null;
  status: string;
  error: string | null;
  nodes: number;
  has_methods: number | null;
  type?: string | null;
  type_source?: string | null;
  authors?: string | null;
  journal?: string | null;
  year?: string | null;
}

interface NodeRow {
  node_id: string;
  type: string;
  label: string;
  role: string;
  heading: string | null;
  text: string;
  page: number | null;
  cites?: number[];
  children: NodeRow[];
}

const request = (page: Page, op: string, params: Record<string, unknown> = {}) =>
  page.evaluate(([op, params]) => (window as unknown as { litrag: { request: (op: string, p: Record<string, unknown>) => Promise<unknown> } }).litrag.request(op, params), [op, params] as const);

const flat = (n: NodeRow): NodeRow[] => [n, ...n.children.flatMap(flat)];
const titleOk = (t: string) => t.trim().split(/\s+/).length >= 4 && !GENERIC.has(t.trim().toLowerCase().replace(/[.:]+$/, '')) && !/^(doi|sha)[_:]/.test(t);

let app: ElectronApplication;
let page: Page;
let root: string;
let lib: string;
const papers = papersToTest();
const minMethods = Number(process.env['LITRAG_E2E_MIN_METHODS'] ?? (process.env['LITRAG_E2E_PAPERS'] ? 0.7 : 1));
const minTitles = Number(process.env['LITRAG_E2E_MIN_TITLES'] ?? (process.env['LITRAG_E2E_PAPERS'] ? 0.9 : 1));

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'litrag-e2e-'));
  app = await electron.launch({ args: [APP_DIR, '--no-sandbox'], env: { ...process.env, LITRAG_ROOT: root } });
  page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
});

test.afterAll(async () => {
  await app?.close();
});

test('the window opens and the worker answers', async () => {
  await expect(page).toHaveTitle('litrag');
  await expect(page.locator('#worker-status.ok, #worker-status.bad')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('#worker-status')).toHaveClass(/ok/);
  const hello = (await request(page, 'hello')) as { worker: string; python: string };
  expect(hello.worker).toMatch(/^\d+\.\d+/);
});

test('a library is made through the button', async () => {
  await page.evaluate(() => {
    window.prompt = () => 'E2E Library';
  });
  await page.click('#new-library');
  await expect(page.locator('#library')).toHaveValue('e2e-library', { timeout: 20_000 });
  lib = await page.locator('#library').inputValue();
});

test(`every paper is ingested and parsed (${papers.length})`, async () => {
  test.setTimeout(30 * 60 * 1000);
  await request(page, 'ingest', { lib, paths: papers });
  await expect(page.locator('#papers .paper')).toHaveCount(papers.length, { timeout: 60_000 });
  await expect(page.locator('#papers .paper .badge.parsing, #papers .paper .badge.queued')).toHaveCount(0, { timeout: 25 * 60 * 1000 });
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  const failed = rows.filter((p) => p.status !== 'parsed');
  expect(failed.map((p) => `${p.key}: ${p.error}`), 'papers that did not parse').toEqual([]);
  expect(rows.every((p) => p.nodes > 20), 'every paper has a tree of some size').toBe(true);
});

test('titles are titles, methods are found, front matter is a few typed nodes', async () => {
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  const untitled = rows.filter((p) => !titleOk(p.title));
  expect(untitled.length / rows.length, `papers without a title: ${untitled.map((p) => `${p.key} ${JSON.stringify(p.title)}`).join('; ')}`).toBeLessThanOrEqual(1 - minTitles + 1e-9);
  const noMethods = rows.filter((p) => !p.has_methods && !/\b(review|progress|advances?|perspectives?|overview|roadmap|challenges|trends)\b/i.test(p.title));
  expect(1 - noMethods.length / rows.length, `no methods section in: ${noMethods.map((p) => p.key).join(', ')}`).toBeGreaterThanOrEqual(minMethods - 1e-9);
  const cards = page.locator('#papers .paper');
  await expect(cards.locator('.badge.parsed')).toHaveCount(rows.length);
  if (!process.env['LITRAG_E2E_PAPERS']) {
    // the fixture is a JATS file: its own article type, contributors, journal and year are on the row and on the card
    const p = rows[0]!;
    expect([p.type, p.type_source], 'a research-article default, confirmed by the shape').toEqual(['research', 'default']);
    expect((JSON.parse(p.authors ?? '[]') as { name: string; affiliations: string[] }[]).filter((a) => a.name && a.affiliations.length).length, 'authors with affiliations from the contributor group').toBeGreaterThanOrEqual(3);
    expect(`${p.journal} ${p.year}`).toBe('Micromachines 2024');
    await expect(cards.first().locator('.byline')).toContainText('Micromachines · 2024');
    await expect(cards.first().locator('.key')).toContainText('research (by default)');
  }
  for (const p of rows) {
    const tree = (await request(page, 'tree', { lib, key: p.key })) as { root: NodeRow };
    const nodes = flat(tree.root);
    const fragments = nodes.filter((n) => n.type === 'paragraph' && n.text.trim().length <= 2);
    expect(fragments.map((n) => `${n.node_id} ${JSON.stringify(n.text)}`), `${p.key}: one-letter paragraphs`).toEqual([]);
    const front = tree.root.children.find((c) => c.type === 'section' && c.heading === 'Front matter');
    if (front) {
      expect(front.children.length, `${p.key}: front matter as ${front.children.length} nodes`).toBeLessThanOrEqual(16); // a Cureus or JKMS first page carries authors, affiliations, ORCIDs, dates, funding and disclosures, each kind its own node
      expect(front.children.every((c) => c.type === 'meta' || c.type === 'picture' || c.type === 'table'), `${p.key}: front matter is typed`).toBe(true);
    }
  }
});

async function openPaper(key: string): Promise<void> {
  await page.locator(`#papers .paper[data-key="${key.replace(/"/g, '\\"')}"]`).click();
  // the tree of *this* paper, not the last one's still on screen: every row's id starts with the key
  await page.waitForFunction((k) => (document.querySelector('#tree .tree-node') as HTMLElement | null)?.dataset['id']?.startsWith(`${k}#`) ?? false, key, { timeout: 30_000 });
}

test('the tree, the detail pane and the page (PDF) or the paper as read (XML)', async () => {
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  // one of each format that is present: the page pane has two faces
  const picks = [rows.find((p) => p.format === 'pdf'), rows.find((p) => p.format === 'jats')].filter((p): p is PaperRow => Boolean(p));
  expect(picks.length).toBeGreaterThan(0);
  for (const row of picks) {
  await openPaper(row.key);
  await expect(page.locator('#tree-title')).toContainText(row.title.slice(0, 20), { ignoreCase: true });
  // a methods paragraph opens the detail pane with its ancestry and its text
  const methods = page.locator('#tree .tree-node.section .role[title="methods"]').first();
  if (await methods.count()) {
    await methods.locator('xpath=..').click();
    const para = page.locator('#tree .tree-node.paragraph .role[title="methods"]').first();
    await para.locator('xpath=..').click();
    await expect(page.locator('#node-detail .crumbs')).not.toBeEmpty();
    await expect(page.locator('#node-detail .text')).not.toBeEmpty();
  }
  if (row.format === 'pdf') {
    await expect(page.locator('#page-overlay .box')).not.toHaveCount(0, { timeout: 30_000 });
    const ink = await page.evaluate(() => {
      const c = document.getElementById('page-canvas') as HTMLCanvasElement;
      if (!c.width) return 0;
      const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data;
      let dark = 0;
      for (let i = 0; i < d.length; i += 4) if (d[i]! < 128 && d[i + 3]! > 0) dark++;
      return dark;
    });
    expect(ink, 'the page drew something').toBeGreaterThan(1000);
  } else {
    await expect(page.locator('#page-reading')).toBeVisible();
    await expect(page.locator('#page-reading .rv.selected')).toHaveCount(1);
    await expect(page.locator('#page-reading h2, #page-reading h3')).not.toHaveCount(0);
  }
  }
});

test('a finding shows the method it was measured by, or says none was found', async () => {
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  let shown = 0;
  for (const row of rows) {
    await openPaper(row.key);
    const results = page.locator('#tree .tree-node.section .role[title="results"], #tree .tree-node.section .role[title="results-discussion"]').first();
    if (!(await results.count())) continue;
    await results.locator('xpath=..').click();
    const para = page.locator('#tree .tree-node.paragraph .role[title="results"], #tree .tree-node.paragraph .role[title="results-discussion"]').first();
    if (!(await para.count())) continue;
    await para.locator('xpath=..').click();
    // the edges arrive after the detail: a "Measured by" box, with links or with the reason there are none
    const box = page.locator('#node-detail .links.edges').first();
    await expect(box).toBeVisible({ timeout: 15_000 });
    await expect(box.locator('.links-head')).toContainText(/Measured by|Cites/);
    const link = box.locator('.link.go').first();
    if (!process.env['LITRAG_E2E_PAPERS']) await expect(link, 'the fixture paper links its first finding').toHaveCount(1);
    if (await link.count()) {
      await link.click();
      await expect(page.locator('#node-detail .meta')).toContainText('methods');
    }
    shown++;
    if (shown >= 2) break;
  }
  expect(shown, 'papers with a results paragraph to select').toBeGreaterThan(0);
});

test('citations are linked both ways', async () => {
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  let linked = 0;
  let withRefs = 0;
  for (const p of rows) {
    const refs = ((await request(page, 'refs', { lib, key: p.key })) as { refs: { ref_no: number; cited_by: string[] }[] }).refs;
    if (!refs.length) continue;
    withRefs++;
    const cited = refs.filter((r) => r.cited_by.length);
    if (cited.length) linked++;
  }
  expect(linked, `papers with citation links, of ${withRefs} with a reference list`).toBeGreaterThanOrEqual(Math.ceil(withRefs * (process.env['LITRAG_E2E_PAPERS'] ? 0.6 : 1)));
  // in the window: a citing node shows "→ n", its detail lists the entries, and the entry opens on a click
  const key = (await page.locator('#papers .paper').first().getAttribute('data-key'))!;
  await openPaper(key);
  const row = page.locator('#tree .tree-node', { has: page.locator('.cites', { hasText: '→' }) }).first();
  if (await row.count()) {
    const id = (await row.getAttribute('data-id'))!;
    await row.click();
    await expect(page.locator('#tree .tree-node.selected')).toHaveAttribute('data-id', id);
    await expect(page.locator('#node-detail .meta')).toContainText(id);
    await expect(page.locator('#node-detail .links .link.go').first()).toBeVisible();
    const entry = page.locator('#node-detail .links .link.go').first();
    const entryNo = (await entry.locator('.tag').textContent())!.trim();
    await entry.click();
    await expect(page.locator('#node-detail .links-head')).toContainText(`Entry ${entryNo}`);
    await expect(page.locator('#tree .tree-node.selected .cites')).toContainText(entryNo);
  }
});

test('the audit finds no errors in the fixture', async () => {
  test.skip(Boolean(process.env['LITRAG_E2E_PAPERS']), 'a folder of real papers is measured by the harness, not gated here');
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  const audit = (await request(page, 'audit', { lib, key: rows[0]!.key })) as { papers: { findings: { severity: string; kind: string; text: string }[] }[] };
  const errors = audit.papers[0]!.findings.filter((f) => f.severity === 'error');
  expect(errors.map((f) => `${f.kind} ‹${f.text}›`)).toEqual([]);
});
