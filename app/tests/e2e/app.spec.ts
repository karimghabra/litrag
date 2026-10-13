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
 *
 * On the fixture, PMC — asked for the paper's PDF when it is filed, then from the Search tab and
 * from a figure's pane — is the stand-in of epmc-fixture.ts (LITRAG_PMC_CLOUD_URL,
 * LITRAG_EPMC_PDF_URL), which holds none, so what the window says of its figures is the same on
 * every machine. It cannot be reached when the paper is filed (`cloudDown`), so the Search tab has
 * a paper to ask PMC for.
 */
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { copyFileSync, existsSync, mkdtempSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { askedDone, figureLabel, figuresOn, paneButton, paneNote, placeholder, stateChip, treeTag, type FigureOutcome, type FigureState, type FigureTag } from '../../src/renderer/figstate.ts';
import { startFixture, type Fixture } from './epmc-fixture.ts';

const APP_DIR = resolve(import.meta.dirname, '..', '..');
const FIXTURE = resolve(APP_DIR, '..', 'parser', 'tests', 'fixtures', 'PMC11278924.xml');
/** A test PDF whose first pages print neither the fixture's DOI nor its title. */
const PAGE_PDF = resolve(APP_DIR, '..', 'parser', 'tests', 'fixtures', 'charts', 'page_paper.pdf');
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
  pmcid?: string | null;
  figures_file?: string | null;
  figure_state?: FigureState | null;
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
/** PMC's stand-in, for the fixture: no PDF of any paper, and out of reach until the Search tab asks it (none on a folder of real papers) */
let pmc: Fixture | undefined;
/** what the stand-in answers when asked for a paper's PDF (epmc-fixture.ts: an empty Cloud listing, the bulk area's 404) */
const NO_PDF = 'PMC Cloud: not in its open-access datasets; bulk area: HTTP 404';
const papers = papersToTest();
const minMethods = Number(process.env['LITRAG_E2E_MIN_METHODS'] ?? (process.env['LITRAG_E2E_PAPERS'] ? 0.7 : 1));
const minTitles = Number(process.env['LITRAG_E2E_MIN_TITLES'] ?? (process.env['LITRAG_E2E_PAPERS'] ? 0.9 : 1));

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'litrag-e2e-'));
  pmc = process.env['LITRAG_E2E_PAPERS'] ? undefined : await startFixture([]);
  if (pmc) pmc.cloudDown = true; // the ask made as the paper is filed cannot reach PMC: it settles nothing
  const asked: Record<string, string> = pmc ? { LITRAG_PMC_CLOUD_URL: pmc.cloudUrl, LITRAG_EPMC_PDF_URL: pmc.pdfUrl } : {};
  app = await electron.launch({ args: [APP_DIR, '--no-sandbox'], env: { ...process.env, LITRAG_ROOT: root, ...asked } });
  page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
});

test.afterAll(async () => {
  await app?.close();
  await pmc?.close();
});

test('the window opens and the worker answers', async () => {
  await expect(page).toHaveTitle('litrag');
  await expect(page.locator('#worker-status.ok, #worker-status.bad')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('#worker-status')).toHaveClass(/ok/);
  const hello = (await request(page, 'hello')) as { worker: string; python: string };
  expect(hello.worker).toMatch(/^\d+\.\d+/);
});

test('a library is made through the button', async () => {
  await page.locator('#nav button[data-view="projects"]').click();
  await page.click('#new-library');
  await page.fill('#np-name', 'E2E Library');
  await page.click('#new-project button[type="submit"]');
  await expect(page.locator('#library')).toHaveValue('e2e-library', { timeout: 20_000 });
  lib = await page.locator('#library').inputValue();
  await page.locator('#nav button[data-view="papers"]').click(); // the rest of this suite is the Papers tab
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
    await expect(cards.first().locator('.kind-row .type-pill')).toHaveText('research'); // every card says what kind of paper it is
    await expect(cards.first().locator('.kind-row .type-by')).toHaveText('by default');
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
    // every figure the reading draws takes a PDF dropped on it, for this paper's figures (figuredrop.ts), and so does its pane of numbers
    const figures = page.locator('#page-reading figure.picture');
    if (!process.env['LITRAG_E2E_PAPERS']) await expect(figures, 'the fixture paper draws its figures').not.toHaveCount(0);
    await expect(page.locator(`#page-reading figure.picture:not([data-figures-for="${row.key}"])`)).toHaveCount(0);
    if (await figures.count()) {
      await figures.first().click();
      await expect(page.locator(`#node-detail .charts[data-figures-for="${row.key}"]`)).toBeVisible({ timeout: 15_000 });
    }
  }
  await expectFigureWords(row.key);
  }
});

/** What a paper's figures came to, in the window's words (figstate.ts) from what the worker answers, whatever they
 *  came to here: the chip on its card, each figure's name and tag in the tree and its placeholder in the reading, and
 *  under a figure's pane of numbers the line and the one button offered (Read the figures only for a figure not read).
 *  A read may still be under way (a PDF PMC gave, kept beside the XML), so the two are compared until they agree. */
async function expectFigureWords(key: string): Promise<void> {
  const soon = { timeout: 5_000 };
  await expect(async () => {
    const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
    const row = rows.find((p) => p.key === key)!;
    const chip = stateChip(row);
    const figs = page.locator(`#papers .paper[data-key="${key.replace(/"/g, '\\"')}"] .figs`);
    if (chip) {
      await expect(figs).toHaveText(chip.text, soon);
      await expect(figs).toHaveAttribute('title', chip.title, soon);
      await expect(figs).toHaveClass(new RegExp(`\\bk-${row.figure_state!.kind}\\b`), soon);
    } else await expect(figs).toHaveCount(0, soon);
    const tree = (await request(page, 'tree', { lib, key })) as { root: NodeRow; figures?: Record<string, FigureTag> };
    const shown = flat(tree.root).filter((n) => n.type === 'picture' || n.type === 'chart');
    if (!process.env['LITRAG_E2E_PAPERS'] && row.format === 'jats') expect(shown.length, 'the fixture paper has figures').toBeGreaterThan(0);
    let first: string | null = null;
    for (const n of shown) {
      const tr = page.locator(`#tree .tree-node[data-id="${n.node_id}"]`);
      if (!(await tr.count())) continue; // under a section folded away
      await expect(tr.locator('.label')).toHaveText(figureLabel(tree.figures, n) ?? /./, soon);
      const tag = treeTag(tree.figures, n);
      if (tag) await expect(tr.locator('.figout')).toHaveText(tag.text, soon);
      else await expect(tr.locator('.figout')).toHaveCount(0, soon);
      if (row.format === 'jats' && n.type === 'picture') await expect(page.locator(`#page-reading figure.picture[data-id="${n.node_id}"] .placeholder`)).toHaveText(placeholder(tree.figures?.[n.node_id], row), soon);
      first ??= n.node_id;
    }
    if (!first) return;
    await page.locator(`#tree .tree-node[data-id="${first}"]`).click();
    const pane = page.locator('#node-detail .charts');
    await expect(pane).toBeVisible({ timeout: 15_000 });
    const { outcome } = (await request(page, 'charts', { lib, figure: first })) as { outcome?: FigureOutcome | null };
    const { figures_on } = (await request(page, 'wanted', { lib })) as { figures_on?: boolean };
    const note = paneNote(outcome, row, figuresOn(figures_on, rows));
    if (note.line) await expect(pane.locator(':scope > .muted').first()).toHaveText(note.line, soon);
    else await expect(pane.locator(':scope > .muted')).toHaveCount(0, soon);
    for (const b of ['read', 'ask', 'ask-again'] as const) await expect(pane.getByRole('button', { name: paneButton(b, key)!.label, exact: true })).toHaveCount(note.button === b ? 1 : 0, soon);
  }).toPass({ timeout: 120_000 });
}

test('a PDF dropped on a figure of an XML paper goes to that paper, and is checked there', async () => {
  test.skip(Boolean(process.env['LITRAG_E2E_PAPERS']), 'a test PDF dropped on the fixture paper\'s figure');
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  const row = rows.find((p) => p.format === 'jats')!;
  await openPaper(row.key);
  const figure = page.locator(`#page-reading figure.picture[data-figures-for="${row.key}"]`).first();
  await figure.scrollIntoViewIfNeeded();
  const at = (await figure.boundingBox())!;
  const pdf = join(root, 'dropped-on-a-figure.pdf');
  copyFileSync(PAGE_PDF, pdf);
  // a real file, dragged over the figure and dropped: the overlay the drag shows is what the drop lands on
  await page.evaluate(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.id = 'e2e-files';
    input.hidden = true;
    document.body.append(input);
  });
  await page.setInputFiles('#e2e-files', pdf);
  const { landed, over, dropped } = await page.evaluate(({ x, y }) => {
    const dt = new DataTransfer();
    for (const f of (document.getElementById('e2e-files') as HTMLInputElement).files ?? []) dt.items.add(f);
    const drag = (type: string) => new DragEvent(type, { dataTransfer: dt, clientX: x, clientY: y, bubbles: true, cancelable: true });
    const overlay = document.getElementById('drop-overlay')!;
    document.elementFromPoint(x, y)!.dispatchEvent(drag('dragover'));
    const over = overlay.textContent;
    const top = document.elementFromPoint(x, y)!;
    top.dispatchEvent(drag('drop'));
    document.getElementById('e2e-files')!.remove();
    return { landed: top.id || top.parentElement?.id, over, dropped: overlay.textContent };
  }, { x: at.x + at.width / 2, y: at.y + at.height / 2 });
  expect(landed, 'the drop lands on the overlay, not on the figure').toBe('drop-overlay');
  expect(over, 'over a figure, the overlay says where the PDF goes').toBe('Drop to keep the PDF for this paper’s figures');
  expect(dropped, 'and says the plain thing again once it is dropped').toBe('Drop to ingest');
  // sent to the paper unasked, and refused there: its first pages do not name it (keep_beside)
  await expect(page.locator('#log .log-line').filter({ hasText: 'dropped on a figure of' })).toHaveCount(1);
  const refused = page.locator('#log .log-line.failed').filter({ hasText: `skipped ${pdf}: its first pages name neither its DOI nor its title` });
  await expect(refused).toHaveCount(1, { timeout: 60_000 });
  await expect(refused.locator('.paper')).toHaveText(row.key);
  const after = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  expect(after.map((p) => p.key), 'nothing filed in its place').toEqual(rows.map((p) => p.key));
  expect(existsSync(pdf), 'a PDF not kept is left where it was').toBe(true);
});

/** the clicked button's own state the moment its click is handled: whether it was disabled at once */
const clickDisables = (b: ReturnType<Page['locator']>) =>
  b.evaluate((el: HTMLButtonElement) => {
    el.click();
    return el.disabled;
  });

const cloudAsks = () => pmc!.requests.filter((r) => r.startsWith('/cloud/')).length;

test('the Search tab asks PMC for the PDFs its XML papers’ figures want, beside Collect PDFs', async () => {
  test.skip(!pmc, 'the fixture paper, asked of PMC’s stand-in');
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  const row = rows.find((p) => p.format === 'jats')!;
  // asked once when it was filed, and PMC could not be reached: nothing settled, so it can be asked
  expect([row.pmcid, row.figure_state?.kind, row.figure_state?.note]).toEqual(['PMC11278924', 'no-pdf', null]);
  expect(row.figure_state?.asked).toMatch(/^could not ask PMC: PMC Cloud: /);
  await page.locator('#nav button[data-view="search"]').click();
  const ask = page.locator('#ask-pmc');
  const collect = page.locator('#collect');
  await expect(collect).toHaveText('Collect PDFs (1 for figures)');
  await expect(ask).toBeVisible();
  await expect(ask).toHaveText('Ask PMC for 1 figure PDF');
  await expect(ask).toBeEnabled();
  // the two sit side by side whatever the candidates' chips take of the head: the pair wraps as one, inside it
  const apart = await page.evaluate(() => {
    const a = document.getElementById('ask-pmc')!;
    const c = document.getElementById('collect')!;
    const head = c.closest('.col-head') as HTMLElement;
    const filler = document.createElement('span');
    filler.style.display = 'inline-block';
    filler.style.height = '1px';
    document.getElementById('cand-filter')!.append(filler);
    const out: string[] = [];
    for (let w = 0; w <= head.clientWidth; w += 4) {
      filler.style.width = `${w}px`;
      const [ra, rc, rh] = [a.getBoundingClientRect(), c.getBoundingClientRect(), head.getBoundingClientRect()];
      if (Math.abs(ra.top - rc.top) > 1 || ra.right > rc.left || rc.left - ra.right > 12 || rc.right > rh.right) out.push(`chips + ${w}px: Ask PMC at (${ra.left}, ${ra.top}), Collect PDFs at (${rc.left}, ${rc.top})`);
    }
    filler.remove();
    return out;
  });
  expect(apart, 'Ask PMC beside Collect PDFs').toEqual([]);
  const line = (said: string) => page.locator('#log .log-line').filter({ hasText: said });
  // PMC still out of reach: the button is disabled at once and until the run ends, then offered again
  const before = cloudAsks();
  expect(await clickDisables(ask), 'one ask a click').toBe(true);
  const down = askedDone({ asked: 1, kept: 0, missed: 0, refused: 0, unreachable: 1 });
  expect(down).toBe('figures: asked PMC for 1, 0 PDFs kept, 0 with none, PMC could not be reached for 1');
  await expect(line(down)).toHaveCount(1, { timeout: 60_000 });
  expect(cloudAsks(), 'the stand-in for PMC was asked').toBeGreaterThan(before);
  await expect(ask).toBeEnabled({ timeout: 15_000 });
  await expect(ask).toHaveText('Ask PMC for 1 figure PDF');
  // PMC there again: it has no PDF of the paper, and says so for good, so the Search tab has none left to ask for
  pmc!.cloudDown = false;
  expect(await clickDisables(ask)).toBe(true);
  const none = askedDone({ asked: 1, kept: 0, missed: 1, refused: 0, unreachable: 0 });
  await expect(line(none)).toHaveCount(1, { timeout: 60_000 });
  await expect(ask).toHaveText('Ask PMC for 0 figure PDFs', { timeout: 15_000 });
  await expect(ask).toBeDisabled();
  await expect(collect).toHaveText('Collect PDFs (1 for figures)'); // still walked by Collect: a person may find the PDF
  await page.locator('#nav button[data-view="papers"]').click();
});

test('a figure no PDF draws asks PMC for the paper’s PDF from its pane, and the log and the pane say what PMC answered', async () => {
  test.skip(!pmc, 'the fixture paper, asked of PMC’s stand-in');
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  const row = rows.find((p) => p.format === 'jats')!;
  // asked from the Search tab: the stand-in has no PDF of it, and said so for good (the paper's note)
  expect([row.pmcid, row.figure_state?.kind, row.figure_state?.note]).toEqual(['PMC11278924', 'no-pdf', NO_PDF]);
  await openPaper(row.key);
  const tree = (await request(page, 'tree', { lib, key: row.key })) as { root: NodeRow; figures?: Record<string, FigureTag> };
  const fig = flat(tree.root).find((n) => n.type === 'picture' && tree.figures?.[n.node_id]?.outcome === 'no-pdf')!;
  expect(fig, 'a figure of the fixture that only a PDF draws').toBeTruthy();
  const figure = page.locator(`#page-reading figure.picture[data-id="${fig.node_id}"]`);
  await figure.scrollIntoViewIfNeeded();
  await figure.click();
  const pane = page.locator(`#node-detail .charts[data-figures-for="${row.key}"]`);
  await expect(pane.locator(':scope > .muted').first()).toHaveText(`PMC has no PDF of it (${NO_PDF}). A PDF of the paper draws its figures: drop one on this figure, or use Collect PDFs on the Search tab.`);
  const again = pane.getByRole('button', { name: paneButton('ask-again', row.key)!.label, exact: true });
  await expect(again).toBeEnabled();
  const before = cloudAsks();
  await pane.evaluate((box) => (box.dataset['drawn'] = 'before'));
  const said = askedDone({ asked: 1, kept: 0, missed: 1, refused: 0, unreachable: 0 });
  expect(said).toBe('figures: asked PMC for 1, 0 PDFs kept, 1 with none, PMC could not be reached for 0');
  const lines = page.locator('#log .log-line').filter({ hasText: said });
  const had = await lines.count(); // the Search tab's ask said it once
  expect(await clickDisables(again), 'one ask a click').toBe(true);
  await expect(lines).toHaveCount(had + 1, { timeout: 60_000 });
  expect(cloudAsks(), 'the stand-in for PMC was asked again').toBeGreaterThan(before);
  // the figure's pane drawn again from the paper's row: PMC's answer, and the ask offered once more
  await expect(page.locator('#node-detail .charts:not([data-drawn])')).toHaveCount(1, { timeout: 15_000 });
  await expect(pane.locator(':scope > .muted').first()).toContainText(`PMC has no PDF of it (${NO_PDF}).`);
  await expect(pane.getByRole('button', { name: paneButton('ask-again', row.key)!.label, exact: true })).toBeEnabled();
  const after = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers.find((p) => p.key === row.key)!;
  expect([after.figures_file ?? null, after.figure_state?.note]).toEqual([null, NO_PDF]);
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

interface LabelItem {
  paper: string;
  finding: { node_id: string; text: string };
  candidates: { node_id: string; heading: string | null; paragraphs: { node_id: string; text: string }[]; edge: { evidence: string } | null }[];
}
interface LabelRow { finding: string; method: string; verdict: string; paragraph: string | null; finding_now: string | null }

test('a finding is labelled in the panel, by mouse and by key, and each label is a row', async () => {
  test.skip(Boolean(process.env['LITRAG_E2E_PAPERS']), 'one finding of the fixture, labelled by hand');
  const queue = (await request(page, 'label_queue', { lib, n: 100 })) as { items: LabelItem[] };
  expect(queue.items.length, 'findings to label in the fixture').toBeGreaterThan(1);
  const first = queue.items[0]!;
  await page.click('#label-links');
  const panel = page.locator('#label-panel');
  await expect(panel).toBeVisible();
  await expect(panel.locator('.label-finding')).toHaveText(first.finding.text);
  await expect(page.locator('#label-pos')).toContainText(`1 of ${queue.items.length}`);
  const rows = panel.locator('.cand:not(.none)');
  await expect(rows).toHaveCount(first.candidates.length);
  // every candidate pre-checked from the linker's edges, and the edge's evidence on it
  for (const [i, c] of first.candidates.entries()) {
    await expect(rows.nth(i).locator('input')).toBeChecked({ checked: Boolean(c.edge) });
    if (c.edge) await expect(rows.nth(i).locator('.ev')).toHaveText(c.edge.evidence);
  }
  await expect(page.locator('#label-truth')).toContainText('Nothing labelled yet');
  // the paragraph the finding rests on, marked inside one method (which checks it); another method toggled by its number key
  const k = first.candidates.findIndex((c) => c.paragraphs.length);
  expect(k, 'a candidate with paragraphs').toBeGreaterThanOrEqual(0);
  await rows.nth(k).locator('.twist').click();
  await expect(rows.nth(k).locator('.para')).toHaveCount(first.candidates[k]!.paragraphs.length);
  await rows.nth(k).locator('.para').first().click();
  await expect(rows.nth(k).locator('.para.on')).toHaveCount(1);
  await expect(rows.nth(k).locator('input')).toBeChecked();
  const j = k === 0 ? 1 : 0;
  const before = await rows.nth(j).locator('input').isChecked();
  await page.keyboard.press(String(j + 1));
  await expect(rows.nth(j).locator('input')).toBeChecked({ checked: !before });
  const shots = process.env['LITRAG_E2E_SHOTS'];
  if (shots) await page.screenshot({ path: join(shots, 'label-panel.png') });
  await page.keyboard.press('Enter');
  // saved: the panel moves to the next finding, and the running line counts it
  await expect(page.locator('#label-pos')).toContainText(`2 of ${queue.items.length}`);
  await expect(panel.locator('.label-finding')).toHaveText(queue.items[1]!.finding.text);
  await expect(page.locator('#label-truth')).toContainText('1 labelled');
  if (shots) await page.screenshot({ path: join(shots, 'label-panel-next.png') });
  const want = first.candidates.map((c, i) => {
    const yes = i === k ? true : i === j ? !before : Boolean(c.edge);
    return { method: c.node_id, verdict: yes ? 'yes' : 'no', paragraph: i === k ? c.paragraphs[0]!.node_id : null };
  });
  const rowsOf = async (finding: string) => ((await request(page, 'labels', { lib })) as { labels: LabelRow[] }).labels.filter((r) => r.finding === finding);
  const saved = await rowsOf(first.finding.node_id);
  expect(saved.map((r) => ({ method: r.method, verdict: r.verdict, paragraph: r.paragraph })).sort((a, b) => a.method.localeCompare(b.method))).toEqual([...want].sort((a, b) => a.method.localeCompare(b.method)));
  expect(saved.every((r) => r.finding_now === first.finding.node_id)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();

  // the same finding from its node detail: the editor opens on it as labelled, and "no method" replaces the set
  await openPaper(first.paper);
  await page.locator(`#tree .tree-node[data-id="${first.finding.node_id}"]`).click();
  await page.locator('#node-detail .links.edges .label-act').click();
  await expect(panel).toBeVisible();
  await expect(page.locator('#label-pos')).toHaveText('one finding');
  await expect(panel.locator('.label-note')).toContainText('Labelled before');
  await expect(rows.nth(k).locator('input')).toBeChecked();
  await expect(rows.nth(k).locator('.para.on')).toHaveCount(1); // the marked paragraph, shown unfolded
  await page.keyboard.press('n');
  await expect(panel.locator('.cand.none input')).toBeChecked();
  await expect(rows.locator('input:checked')).toHaveCount(0);
  await page.keyboard.press('Enter');
  await expect(panel).toBeHidden();
  expect((await rowsOf(first.finding.node_id)).map((r) => [r.method, r.verdict])).toEqual([['', 'none']]);
  // the queue picks up where it was left
  await page.click('#label-links');
  await expect(page.locator('#label-pos')).toContainText(`2 of ${queue.items.length}`);
  await page.keyboard.press('Escape');
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

/** a second paper of another type, so the list has something to tell apart: a short review, as JATS */
const REVIEW_XML = `<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="review-article" dtd-version="1.2">
<front>
<journal-meta><journal-title-group><journal-title>Journal of End-to-End Tests</journal-title></journal-title-group></journal-meta>
<article-meta>
<article-id pub-id-type="doi">10.9999/litrag.e2e.review</article-id>
<title-group><article-title>Collagen scaffolds for tendon repair: a review of the last decade</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>Tester</surname><given-names>Ada</given-names></name></contrib></contrib-group>
<pub-date pub-type="epub"><year>2025</year></pub-date>
<abstract><p>This review surveys the collagen scaffolds proposed for tendon repair over the last decade, the chemistries used to crosslink them, and what is known of their behaviour in animals.</p></abstract>
</article-meta>
</front>
<body>
<sec><title>1. Introduction</title><p>Tendon injuries are common and heal slowly, and scaffolds made of collagen have been studied for decades as a way to bridge a gap that sutures alone cannot close, with mixed results in the clinic.</p></sec>
<sec><title>2. Sources of collagen</title><p>Collagen for scaffolds is extracted from bovine tendon, porcine skin and rat tail, and each source gives fibrils of a different diameter and a different content of telopeptides, which matters for the immune response.</p></sec>
<sec><title>3. Crosslinking chemistries</title><p>Genipin, carbodiimide and glutaraldehyde are the crosslinkers reported most often, and they differ in the stiffness they give, in their toxicity to cells and in how long the scaffold survives once it is implanted.</p></sec>
<sec><title>4. Conclusions</title><p>Collagen scaffolds are closer to the clinic than they were ten years ago, and the open questions are sterilisation, scale and the long-term fate of the crosslinks in a loaded tendon.</p></sec>
</body>
<back><ref-list><title>References</title>
<ref id="r1"><label>1</label><mixed-citation>Smith JA, Lee CD. Collagen crosslinking in tendon repair. J Biomed Mater Res A. 2019;107(4):812-821.</mixed-citation></ref>
<ref id="r2"><label>2</label><mixed-citation>Garcia M, Zhang W. Genipin and the mechanics of collagen threads. Acta Biomater. 2021;128:100-110.</mixed-citation></ref>
</ref-list></back>
</article>
`;

test('the audit finds no errors in the fixture', async () => {
  test.skip(Boolean(process.env['LITRAG_E2E_PAPERS']), 'a folder of real papers is measured by the harness, not gated here');
  const rows = ((await request(page, 'papers', { lib })) as { papers: PaperRow[] }).papers;
  const audit = (await request(page, 'audit', { lib, key: rows[0]!.key })) as { papers: { findings: { severity: string; kind: string; text: string }[] }[] };
  const errors = audit.papers[0]!.findings.filter((f) => f.severity === 'error');
  expect(errors.map((f) => `${f.kind} ‹${f.text}›`)).toEqual([]);
});

test('the list sorts by format, type, title or year, and narrows to one type', async () => {
  test.skip(Boolean(process.env['LITRAG_E2E_PAPERS']), 'told apart on the fixture library: one research paper, one review');
  const review = join(root, 'e2e-review.xml');
  writeFileSync(review, REVIEW_XML);
  await request(page, 'ingest', { lib, paths: [review] });
  await expect(page.locator('#papers .paper')).toHaveCount(2, { timeout: 120_000 });
  await expect(page.locator('#papers .paper .badge.parsing, #papers .paper .badge.queued')).toHaveCount(0, { timeout: 120_000 });
  await expect(page.locator('#papers-tools')).toBeVisible();
  await expect(page.locator('#papers-sort option')).toHaveText(['as added', 'format', 'type', 'title', 'year, newest first', 'confidence, lowest first']);
  await expect(page.locator('#papers .paper .conf')).toHaveCount(2); // every reading is scored, and the card says so
  const chips = page.locator('#type-filter .chip');
  await expect(chips).toHaveText(['research 1', 'review 1'], { timeout: 30_000 });
  await expect(page.locator('#format-filter .chip')).toHaveCount(0); // both are XML: nothing to narrow by format
  await chips.filter({ hasText: 'review' }).click();
  await expect(page.locator('#papers .paper')).toHaveCount(1);
  await expect(page.locator('#papers .paper .title')).toContainText('a review of the last decade');
  await expect(page.locator('#papers-count')).toHaveText('1 of 2');
  await page.locator('#type-filter .chip.on').click();
  await expect(page.locator('#papers .paper')).toHaveCount(2);
  await expect(page.locator('#papers-count')).toHaveText('2');
  await page.locator('#papers-sort').selectOption('title');
  const titles = await page.locator('#papers .paper .title').allTextContents();
  expect(titles).toEqual([...titles].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' })));
  await page.locator('#papers-sort').selectOption('added');
});
