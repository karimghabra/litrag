/**
 * The studio, end to end, the way a person uses it: the real window, the real worker, Docling
 * on real PDFs, the real embedder. Only Europe PMC is stood in for, by a server on 127.0.0.1
 * (epmc-fixture.ts), so a search, a fetch of open XML, a fetch of an open PDF and a paper with
 * nothing open all happen along the real code paths without the network.
 *
 *   1. a project is made from the Projects tab, with a description;
 *   2. a literature search from the Search tab finds three papers: XML, PDF only, nothing open;
 *   3. "Fetch & read" takes the XML, then the open PDF, and marks the third as needing a PDF;
 *   4. the third is downloaded by hand and dropped in: its candidate turns to "in library";
 *   5. the Papers tab: every paper read, a tree, the page with its boxes, the canonical face;
 *   6. the Types tab: the kinds of paper, a canonical structure, a paper's mapping onto it;
 *   7. the Query tab: passages embedded, a question answered with passages hydrated from the
 *      tree — the paragraphs around each, and the methods a finding was measured by;
 *   8. a second project, and both merged into a third: one paper held twice is filed once;
 *   9. the local model drafts search queries from the project's description (LITRAG_E2E_MODELS=0 skips it);
 *  10. nothing went to the log as an error.
 *
 * The PDFs are real papers from outside the repository: LITRAG_E2E_PDF_OPEN and
 * LITRAG_E2E_PDF_CLOSED, by default two of the archive beside the checkout. Needs Docling's
 * models and Ollama with nomic-embed-text; a few minutes on the GPU.
 */
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { startFixture, type Fixture } from './epmc-fixture.ts';

const APP_DIR = resolve(import.meta.dirname, '..', '..');
const XML = resolve(APP_DIR, '..', 'parser', 'tests', 'fixtures', 'PMC11278924.xml');
const ARCHIVE = resolve(APP_DIR, '..', '..', 'litrag-archive', 'looped-ligament', 'papers');
const PDF_OPEN = process.env['LITRAG_E2E_PDF_OPEN'] ?? join(ARCHIVE, 'doi_10.1016_j.actbio.2017.05.058.pdf');
const PDF_CLOSED = process.env['LITRAG_E2E_PDF_CLOSED'] ?? join(ARCHIVE, 'doi_10.1016_j.biomaterials.2008.04.028.pdf');
const MODELS = process.env['LITRAG_E2E_MODELS'] !== '0';

const request = <T = Record<string, unknown>>(page: Page, op: string, params: Record<string, unknown> = {}) =>
  page.evaluate(([op, params]) => (window as unknown as { litrag: { request: (op: string, p: Record<string, unknown>) => Promise<unknown> } }).litrag.request(op, params), [op, params] as const) as Promise<T>;

interface Cand { cand_id: number; doi: string | null; status: string; title: string | null; paper_key: string | null }
interface PaperRow { key: string; title: string; status: string; format: string | null; nodes: number; has_methods: number | null; type: string | null; error: string | null }

let app: ElectronApplication;
let page: Page;
let root: string;
let fixture: Fixture;
const LIB = 'e2e-studio';

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  for (const f of [PDF_OPEN, PDF_CLOSED]) if (!existsSync(f)) throw new Error(`the suite reads two real PDFs; ${f} is not there (set LITRAG_E2E_PDF_OPEN / LITRAG_E2E_PDF_CLOSED)`);
  fixture = await startFixture([
    { pmcid: 'PMC11278924', pmid: '39064362', doi: '10.3390/mi15070851', title: 'Computational and Experimental Characterization of Aligned Collagen across Varied Crosslinking Degrees', authors: 'Nijhawan A, Akkus O, et al.', journal: 'Micromachines', year: '2024', abstract: 'Collagen threads aligned electrochemically and crosslinked with genipin at varied degrees, characterised mechanically and computationally.', open: true, inEPMC: true, xml: XML },
    { pmcid: 'PMC9000001', doi: '10.1016/j.actbio.2017.05.058', title: 'Effects of substrate stiffness on the tenoinduction of human mesenchymal stem cells', authors: 'Islam A, Younesi M, Mbimba T, Akkus O', journal: 'Acta Biomaterialia', year: '2017', abstract: 'Substrate stiffness and the tenogenic differentiation of stem cells on collagen threads.', open: true, inEPMC: false, pdf: PDF_OPEN },
    { pmid: '18499248', doi: '10.1016/j.biomaterials.2008.04.028', title: 'An electrochemical fabrication process for the assembly of anisotropically oriented collagen bundles', authors: 'Cheng X, Gurkan UA, Dehen CJ, Tate MP, Hillhouse HW, Simpson GJ, Akkus O', journal: 'Biomaterials', year: '2008', abstract: 'Electrochemical alignment of collagen into dense, oriented bundles.', open: false, inEPMC: false },
  ]);
  root = mkdtempSync(join(tmpdir(), 'litrag-studio-'));
  app = await electron.launch({
    args: [APP_DIR, '--no-sandbox'],
    env: { ...process.env, LITRAG_ROOT: root, LITRAG_EPMC_URL: fixture.url, LITRAG_EPMC_PDF_URL: fixture.pdfUrl },
  });
  page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
  // a fresh window starts on the Projects tab: nothing is remembered from another run
  await page.evaluate(() => window.localStorage.clear());
});

test.afterAll(async () => {
  await app?.close();
  await fixture?.close();
});

const tab = (name: string) => page.locator(`#nav button[data-view="${name}"]`).click();

async function candidates(lib = LIB): Promise<Cand[]> {
  return (await request<{ candidates: Cand[] }>(page, 'candidates', { lib })).candidates;
}

async function papersOf(lib = LIB): Promise<PaperRow[]> {
  return (await request<{ papers: PaperRow[] }>(page, 'papers', { lib })).papers;
}

async function makeProject(name: string, description: string): Promise<void> {
  await tab('projects');
  await page.click('#new-library');
  await page.fill('#np-name', name);
  await page.fill('#np-description', description);
  await page.click('#new-project button[type="submit"]');
}

test('1. the window opens on Projects, and a project is made there', async () => {
  await expect(page).toHaveTitle('litrag');
  await expect(page.locator('#worker-status')).toHaveClass(/ok/, { timeout: 60_000 });
  await expect(page.locator('#view-projects')).toBeVisible();
  await makeProject('E2E Studio', 'Electrochemically aligned collagen threads for tendon and ligament repair: crosslinking with genipin, mechanical properties, and stem-cell tenoinduction.');
  await expect(page.locator('#library')).toHaveValue(LIB, { timeout: 20_000 });
  const card = page.locator(`#projects .project[data-id="${LIB}"]`);
  await expect(card).toBeVisible();
  await expect(card.locator('.desc')).toContainText('Electrochemically aligned collagen');
});

test('2. a literature search finds three papers, and says what can be had of each', async () => {
  await tab('search');
  await page.fill('#search-q', '"electrochemically aligned collagen" AND (genipin OR tendon)');
  await page.click('#search-form button[type="submit"]');
  const hits = page.locator('#search-results .hit');
  await expect(hits).toHaveCount(3, { timeout: 30_000 });
  await expect(page.locator('#search-results .pill.xml')).toHaveCount(1);
  await expect(page.locator('#search-results .pill.pdf')).toHaveCount(1);
  await expect(page.locator('#search-results .pill.closed')).toHaveCount(1);
  await expect(page.locator('#candidates .hit')).toHaveCount(3); // every hit is kept as a candidate
  const manifest = (await request<{ projects: { id: string; queries: { query: string }[] }[] }>(page, 'projects')).projects.find((p) => p.id === LIB)!;
  expect(manifest.queries.map((q) => q.query)).toEqual(['"electrochemically aligned collagen" AND (genipin OR tendon)']);
  // the same search again adds nothing twice
  await page.click('#search-form button[type="submit"]');
  await expect(hits).toHaveCount(3, { timeout: 30_000 });
  expect((await candidates()).length).toBe(3);
});

test('3. Fetch & read: the XML, then the open PDF; the closed paper is marked as needing a PDF', async () => {
  test.setTimeout(20 * 60 * 1000);
  await page.check('#search-all');
  await expect(page.locator('#search-fetch')).toHaveText('Fetch & read 3');
  await page.click('#search-fetch');
  await expect(page.locator('#activity')).toBeVisible({ timeout: 10_000 });
  await expect.poll(async () => (await candidates()).map((c) => c.status).sort().join(','), { timeout: 15 * 60 * 1000, intervals: [2000] }).toBe('ingested,ingested,needs-pdf');
  const byDoi = new Map((await candidates()).map((c) => [c.doi, c]));
  expect(byDoi.get('10.3390/mi15070851')!.paper_key).toBe('doi:10.3390/mi15070851');
  expect(byDoi.get('10.1016/j.actbio.2017.05.058')!.paper_key).toMatch(/actbio\.2017\.05\.058/i);
  expect(fixture.requests.some((r) => r.includes('/PMC11278924/fullTextXML'))).toBe(true);
  expect(fixture.requests.some((r) => r.includes('/PMC9000001.zip'))).toBe(true);
  await expect(page.locator('#candidates .pill.st-needs-pdf')).toHaveCount(1, { timeout: 30_000 });
  await expect(page.locator('#candidates .pill.st-ingested')).toHaveCount(2);
  // the closed paper offers its publisher's page to download from
  await expect(page.locator('#candidates .hit', { has: page.locator('.pill.st-needs-pdf') }).locator('a', { hasText: 'publisher' })).toHaveCount(1);
});

test('4. the closed paper, downloaded by hand and dropped in, is filed against its candidate', async () => {
  test.setTimeout(10 * 60 * 1000);
  await request(page, 'ingest', { lib: LIB, paths: [PDF_CLOSED] });
  await expect.poll(async () => (await candidates()).filter((c) => c.status === 'ingested').length, { timeout: 8 * 60 * 1000, intervals: [2000] }).toBe(3);
  await expect.poll(async () => (await papersOf()).filter((p) => p.status === 'parsed').length, { timeout: 8 * 60 * 1000, intervals: [2000] }).toBe(3);
});

test('5. Papers: every paper read into a tree, the page drawn, the canonical face', async () => {
  await tab('papers');
  await expect(page.locator('#papers .paper')).toHaveCount(3, { timeout: 30_000 });
  await expect(page.locator('#papers .paper .badge.parsed')).toHaveCount(3);
  const rows = await papersOf();
  expect(rows.map((p) => `${p.key}: ${p.status} ${p.error ?? ''}`).filter((s) => !s.includes('parsed'))).toEqual([]);
  expect(rows.every((p) => p.nodes > 20), 'every paper has a tree of some size').toBe(true);
  expect(rows.filter((p) => p.has_methods).length, 'three research papers, each with its methods found').toBe(3);
  for (const fmt of ['pdf', 'jats']) {
    const row = rows.find((p) => p.format === fmt)!;
    await page.locator(`#papers .paper[data-key="${row.key}"]`).click();
    await page.waitForFunction((k) => (document.querySelector('#tree .tree-node') as HTMLElement | null)?.dataset['id']?.startsWith(`${k}#`) ?? false, row.key, { timeout: 30_000 });
    if (fmt === 'pdf') await expect(page.locator('#page-overlay .box')).not.toHaveCount(0, { timeout: 30_000 });
    else await expect(page.locator('#page-reading')).toBeVisible();
    await page.click('#tree-mode button[data-mode="canonical"]');
    await expect(page.locator('#canonical .slot')).not.toHaveCount(0, { timeout: 20_000 });
    for (const slot of ['introduction', 'methods', 'results']) await expect(page.locator(`#canonical .slot[data-slot="${slot}"] .slot-sec`).first(), `${row.key}: its ${slot}`).toBeVisible();
    // a section in the canonical face opens in the tree
    await page.locator('#canonical .slot[data-slot="methods"] .slot-sec').first().click();
    await expect(page.locator('#node-detail .meta')).toContainText('methods');
    await page.click('#tree-mode button[data-mode="printed"]');
  }
});

test('6. Types: the kinds of paper, a canonical structure, and a paper mapped onto it', async () => {
  await tab('types');
  await expect(page.locator('#types-list .type-tab')).not.toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator('#types-list .type-tab[data-type="research"] .n')).toHaveText('3');
  for (const slot of ['abstract', 'introduction', 'methods', 'results', 'discussion']) await expect(page.locator(`#skeleton .skel-slot[data-slot="${slot}"]`), `the research skeleton has ${slot}`).toHaveCount(1);
  await expect(page.locator('#mapping .map-sec')).not.toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator('#mapping svg path')).not.toHaveCount(0);
  const first = page.locator('#mapping .map-sec').nth(1);
  const id = (await first.getAttribute('data-id'))!;
  await first.click();
  await expect(page.locator('#view-papers')).toBeVisible();
  await expect(page.locator('#tree .tree-node.selected')).toHaveAttribute('data-id', id, { timeout: 20_000 });
});

test('7. Query: passages embedded, a question answered with its context and its methods', async () => {
  test.setTimeout(10 * 60 * 1000);
  await tab('query');
  await expect(page.locator('#embed-status')).toContainText('passages embedded', { timeout: 20_000 });
  await page.click('#embed');
  await expect.poll(async () => {
    const r = await request<{ units: number; embedded: number }>(page, 'retrieval', { lib: LIB });
    return r.units > 0 && r.embedded === r.units;
  }, { timeout: 8 * 60 * 1000, intervals: [2000] }).toBe(true);
  const status = await request<{ units: number; embedded: number }>(page, 'retrieval', { lib: LIB });
  await expect(page.locator('#embed-status')).toContainText(`${status.embedded.toLocaleString()} of ${status.units.toLocaleString()}`, { timeout: 20_000 });
  // embedding again asks nothing: every passage is already held
  const again = await request<Record<string, number>>(page, 'embed', { lib: LIB });
  expect(again['event']).toBe('queued');

  await page.fill('#query-q', 'How do genipin concentration and crosslinking duration affect the degree of crosslinking of collagen threads?');
  await page.click('#query-form button[type="submit"]');
  const hits = page.locator('#query-results .qhit');
  await expect(hits.first()).toBeVisible({ timeout: 60_000 });
  expect(await hits.count()).toBeGreaterThanOrEqual(3);
  // the best passage is from the genipin paper, and it is hydrated: its headings, its neighbours
  await expect(hits.first().locator('.ptitle')).toContainText('Aligned Collagen');
  await expect(hits.first().locator('.crumb')).not.toBeEmpty();
  await expect(page.locator('#query-results .ctx').first()).toBeVisible();
  // a finding brings the method it was measured by
  await expect(page.locator('#query-results .side-item.methods').first()).toBeVisible();
  const answer = await request<{ hits: { hit: { node_id: string; role: string }; methods: { node_id: string }[]; before: unknown[]; after: unknown[] }[]; embedder: { down: boolean } }>(page, 'query', { lib: LIB, question: 'degree of crosslinking measured by TNBS assay for genipin concentrations', k: 8 });
  expect(answer.embedder.down, 'the embedder answered').toBe(false);
  expect(answer.hits.some((h) => h.methods.length > 0), 'some hit carries its methods').toBe(true);
  expect(answer.hits.every((h) => Array.isArray(h.before) && Array.isArray(h.after))).toBe(true);
  // "Open in the tree" lands on the passage
  const nodeId = (await hits.first().getAttribute('data-node'))!;
  await hits.first().locator('.qhit-foot button').click();
  await expect(page.locator('#view-papers')).toBeVisible();
  await expect(page.locator('#tree .tree-node.selected')).toHaveAttribute('data-id', nodeId, { timeout: 20_000 });
});

const REVIEW_XML = `<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="review-article" dtd-version="1.2">
<front><journal-meta><journal-title-group><journal-title>Journal of End-to-End Tests</journal-title></journal-title-group></journal-meta>
<article-meta><article-id pub-id-type="doi">10.9999/litrag.e2e.review</article-id>
<title-group><article-title>Collagen scaffolds for tendon repair: a review of the last decade</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>Tester</surname><given-names>Ada</given-names></name></contrib></contrib-group>
<pub-date pub-type="epub"><year>2025</year></pub-date>
<abstract><p>This review surveys the collagen scaffolds proposed for tendon repair over the last decade, the chemistries used to crosslink them, and what is known of their behaviour in animals.</p></abstract>
</article-meta></front>
<body>
<sec><title>1. Introduction</title><p>Tendon injuries are common and heal slowly, and scaffolds made of collagen have been studied for decades as a way to bridge a gap that sutures alone cannot close, with mixed results in the clinic.</p></sec>
<sec><title>2. Crosslinking chemistries</title><p>Genipin, carbodiimide and glutaraldehyde are the crosslinkers reported most often, and they differ in the stiffness they give, in their toxicity to cells and in how long the scaffold survives once it is implanted.</p></sec>
<sec><title>3. Conclusions</title><p>Collagen scaffolds are closer to the clinic than they were ten years ago, and the open questions are sterilisation, scale and the long-term fate of the crosslinks in a loaded tendon.</p></sec>
</body>
<back><ref-list><title>References</title><ref id="r1"><label>1</label><mixed-citation>Smith JA, Lee CD. Collagen crosslinking in tendon repair. J Biomed Mater Res A. 2019;107(4):812-821.</mixed-citation></ref></ref-list></back>
</article>
`;

test('8. a second project, and both merged into a third: a paper held twice is filed once', async () => {
  test.setTimeout(10 * 60 * 1000);
  await makeProject('E2E Second', 'Collagen scaffolds for tendon repair, reviewed.');
  await expect(page.locator('#library')).toHaveValue('e2e-second', { timeout: 20_000 });
  const review = join(root, 'e2e-review.xml');
  writeFileSync(review, REVIEW_XML);
  await request(page, 'ingest', { lib: 'e2e-second', paths: [review, XML] }); // the genipin paper again: held by both
  await expect.poll(async () => (await papersOf('e2e-second')).filter((p) => p.status === 'parsed').length, { timeout: 5 * 60 * 1000, intervals: [1000] }).toBe(2);

  await tab('projects');
  await page.click('#merge-open');
  await page.check(`#merge-sources input[value="${LIB}"]`);
  await page.check('#merge-sources input[value="e2e-second"]');
  await page.fill('#merge-name', 'E2E Merged');
  await page.click('#merge-form button[type="submit"]');
  await expect(page.locator('#library')).toHaveValue('e2e-merged', { timeout: 5 * 60 * 1000 });
  const merged = await papersOf('e2e-merged');
  expect(merged.length, 'three papers and a review; the genipin paper once').toBe(4);
  expect(merged.filter((p) => p.status === 'parsed').length).toBe(4);
  expect(merged.every((p) => p.nodes > 5), 'every merged paper has its rows derived again').toBe(true);
  expect(merged.map((p) => p.key).sort()).toEqual([...new Set(merged.map((p) => p.key))].sort());
  const card = page.locator('#projects .project[data-id="e2e-merged"]');
  await expect(card).toBeVisible();
  await expect(card.locator('.stats')).toContainText('4 papers');
  // the merged project's types now hold a review as well
  await tab('types');
  await expect(page.locator('#types-list .type-tab[data-type="review"]')).toHaveCount(1, { timeout: 20_000 });
  // the sources are left as they were
  expect((await papersOf(LIB)).length).toBe(3);
  expect((await papersOf('e2e-second')).length).toBe(2);
});

test('9. the local model drafts searches from the project’s description', async () => {
  test.skip(!MODELS, 'LITRAG_E2E_MODELS=0');
  test.setTimeout(5 * 60 * 1000);
  await page.selectOption('#library', LIB);
  await tab('search');
  await page.click('#search-suggest');
  await expect(page.locator('#search-suggestions .chip').first()).toBeVisible({ timeout: 4 * 60 * 1000 });
});

test('10. nothing went to the log as an error', async () => {
  const errors = await page.locator('#log .log-line.error, #log .log-line.failed').allTextContents();
  expect(errors).toEqual([]);
});
