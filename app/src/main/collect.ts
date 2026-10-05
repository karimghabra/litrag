/**
 * The collect window: one browser window through the papers that want a PDF.
 *
 * A paper with no open full text is a candidate marked `needs-pdf`. This walks them one at a
 * time: the paper's page opens (its DOI, else PubMed, else Europe PMC), the person signs in the
 * way they would in any browser and clicks the PDF, and the moment a download starts it is
 * routed into the project's inbox under the paper's key — so the file's name is the paper's
 * identity — while the window moves on to the next. When the download finishes, the renderer is
 * told, and asks the worker to read the file as it reads any dropped one.
 *
 * The click stays the person's: nothing here signs in, solves a challenge or fetches behind a
 * login. The session is persistent (`persist:litrag-collect`), so an institutional sign-in
 * survives from one run to the next. Popups fold back into the one window; a challenge page that
 * wedges the renderer is crashed and loaded again; Ctrl+Right skips, Alt+Left goes back,
 * Ctrl+Home reopens the paper's page, Ctrl+W finishes. Ported from Protracker's (#61), which
 * ported litrag's own `lit collect`.
 *
 * Only files are written here, into the inbox — never the store (invariant 2).
 */

import { BrowserWindow, Menu, session, type DownloadItem } from 'electron';
import { closeSync, openSync, readSync, unlinkSync } from 'node:fs';
import { extname, join } from 'node:path';

export interface CollectPaper {
  cand_id: number;
  title: string | null;
  doi: string | null;
  pmid: string | null;
  pmcid: string | null;
  /** where OpenAlex says an open copy is (a repository, the publisher's open PDF): opened first */
  open?: string | null;
}

export interface CollectJob {
  lib: string;
  inboxDir: string;
  papers: CollectPaper[];
}

export type CollectEvent = Record<string, unknown> & { event: 'collect' };

const EXTENSIONS = ['.pdf', '.xml'];

/** A paper key as a file name — the worker's `safe_key`, so the file is filed under the paper it was caught for. */
export function fileNameFor(p: CollectPaper, ext: string): string {
  const key = p.doi ? `doi:${p.doi}` : p.pmid ? `pmid:${p.pmid}` : p.pmcid ? `pmcid:${p.pmcid}` : `cand:${p.cand_id}`;
  return key.replace(/[^A-Za-z0-9._-]+/g, '_') + ext;
}

/** The page a person opens to get the paper: an open copy OpenAlex knows of (measured 2026-10-04: 56 of
 *  99 papers no service would give out had one), else its DOI, else PubMed, else Europe PMC.
 *  `LITRAG_DOI_RESOLVER` stands another resolver in for doi.org (the end-to-end suite serves one on
 *  127.0.0.1). A fetch asked the open copy already and was refused it (a bot check, most often): the
 *  person's browser is let in where a program is not. */
export function linkFor(p: CollectPaper, env: NodeJS.ProcessEnv = process.env): string | null {
  const resolver = (env['LITRAG_DOI_RESOLVER'] || 'https://doi.org/').replace(/\/?$/, '/');
  if (p.open && /^https?:\/\//.test(p.open)) return p.open;
  if (p.doi) return resolver + p.doi;
  if (p.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`;
  if (p.pmcid) return `https://europepmc.org/article/PMC/${p.pmcid}`;
  return null;
}

/** Whether a caught file is what it claims: a PDF starts `%PDF`, an XML starts with `<`. A publisher
 *  that answers the PDF link with a login page must not be filed as the paper. */
function looksRight(path: string, ext: string): boolean {
  try {
    const fd = openSync(path, 'r');
    const head = Buffer.alloc(512);
    const n = readSync(fd, head, 0, 512, 0);
    closeSync(fd);
    const text = head.subarray(0, n).toString('latin1').trimStart();
    return ext === '.pdf' ? text.startsWith('%PDF') : text.startsWith('<');
  } catch {
    return false;
  }
}

let open: BrowserWindow | null = null;

export function runCollect(job: CollectJob, send: (e: CollectEvent) => void): Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    const papers = job.papers.filter((p) => linkFor(p));
    const unlinked = job.papers.length - papers.length;
    if (!papers.length) {
      resolve({ ok: true, papers: 0, unlinked, caught: 0, message: 'Nothing is waiting for a PDF that has a page to open.' });
      return;
    }
    if (open && !open.isDestroyed()) {
      open.focus();
      resolve({ ok: false, message: 'A collect window is already open.' });
      return;
    }
    let index = 0;
    let caught = 0;
    let skipped = 0;
    let finished = false;
    let loadGen = 0;
    let lastCommit = 0;
    const ses = session.fromPartition('persist:litrag-collect');
    const win = new BrowserWindow({
      width: 1200,
      height: 900,
      title: 'Collect PDFs',
      // no `plugins: true`: without the PDF viewer, a PDF the page serves inline becomes a download — the whole point
      webPreferences: { partition: 'persist:litrag-collect', sandbox: true },
    });
    open = win;

    const status = (extra: Record<string, unknown> = {}) =>
      send({ event: 'collect', lib: job.lib, index, total: papers.length, caught, skipped, unlinked, ...extra });

    const finish = () => {
      if (finished) return;
      finished = true;
      open = null;
      ses.removeListener('will-download', onDownload);
      if (!win.isDestroyed()) win.close();
      const summary = { ok: true, papers: papers.length, unlinked, caught, skipped };
      status({ stage: 'finished', ...summary });
      resolve(summary);
    };

    const show = () => {
      const p = papers[index]!;
      win.setTitle(`Collect ${index + 1} of ${papers.length} — ${p.title ?? p.doi ?? ''}`);
      status({ stage: 'opened', cand_id: p.cand_id, title: p.title });
      // a challenge page can wedge the renderer: if nothing commits in six seconds, crash it and load again
      const gen = ++loadGen;
      win.webContents.stop();
      void win.loadURL(linkFor(p)!).catch(() => {});
      setTimeout(() => {
        if (finished || gen !== loadGen || lastCommit >= gen || win.isDestroyed()) return;
        win.webContents.forcefullyCrashRenderer();
        void win.loadURL(linkFor(p)!).catch(() => {});
      }, 6000);
    };

    const advance = () => {
      index += 1;
      if (index >= papers.length) return finish();
      show();
    };

    const onDownload = (_event: unknown, item: DownloadItem) => {
      if (finished) return;
      const p = papers[Math.min(index, papers.length - 1)]!;
      const given = extname(item.getFilename() ?? '').toLowerCase();
      const ext = EXTENSIONS.includes(given) ? given : '.pdf'; // a rare XML stays an XML; "download.php" is the PDF it served
      const path = join(job.inboxDir, fileNameFor(p, ext));
      item.setSavePath(path);
      status({ stage: 'downloading', cand_id: p.cand_id, title: p.title });
      item.once('done', (_e, state) => {
        if (state !== 'completed') {
          status({ stage: 'download-failed', cand_id: p.cand_id, title: p.title, reason: state });
          return;
        }
        if (!looksRight(path, ext)) {
          try {
            unlinkSync(path);
          } catch {
            // already gone
          }
          status({ stage: 'not-a-paper', cand_id: p.cand_id, title: p.title, reason: `what came back is not a ${ext.slice(1).toUpperCase()} (a sign-in page?)` });
          return;
        }
        caught += 1;
        status({ stage: 'caught', cand_id: p.cand_id, title: p.title, path, known: { doi: p.doi, pmid: p.pmid, pmcid: p.pmcid } });
      });
      advance();
    };
    ses.on('will-download', onDownload);

    // the title is the walk's position, not whatever the page claims ("Just a moment…")
    win.on('page-title-updated', (event) => event.preventDefault());
    const skip = () => {
      skipped += 1;
      advance();
    };
    // the window's own menu: the walk's verbs, and nothing that can take the app down
    win.setMenu(
      Menu.buildFromTemplate([
        {
          label: 'Collect',
          submenu: [
            { label: 'Skip this paper', accelerator: 'CmdOrCtrl+Right', registerAccelerator: false, click: skip },
            { label: 'Back', accelerator: 'Alt+Left', registerAccelerator: false, click: () => win.webContents.navigationHistory.goBack() },
            { label: 'Reopen the paper’s page', accelerator: 'CmdOrCtrl+Home', registerAccelerator: false, click: () => show() },
            { type: 'separator' },
            { label: 'Finish collecting', accelerator: 'CmdOrCtrl+W', registerAccelerator: false, click: () => finish() },
          ],
        },
        { role: 'editMenu' },
      ]),
    );
    win.webContents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return;
      const mod = input.control || input.meta;
      if (mod && input.key === 'ArrowRight') {
        event.preventDefault();
        skip();
      } else if (input.alt && input.key === 'ArrowLeft') {
        event.preventDefault();
        win.webContents.navigationHistory.goBack();
      } else if (mod && input.key === 'Home') {
        event.preventDefault();
        show();
      } else if (mod && (input.key === 'w' || input.key === 'W')) {
        event.preventDefault();
        finish();
      }
    });
    win.webContents.setWindowOpenHandler(({ url }) => {
      void win.loadURL(url).catch(() => {});
      return { action: 'deny' };
    });
    win.webContents.on('did-navigate', () => {
      lastCommit = loadGen;
    });
    win.on('closed', finish);
    show();
  });
}
