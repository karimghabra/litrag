/**
 * Electron's main process: one window, one parsing worker, and the IPC
 * between them. The renderer never touches the file system or the worker
 * directly; it asks here, and every worker event is forwarded to it.
 */

import { app, BrowserWindow, dialog, ipcMain, session, shell } from 'electron';
import { readFile } from 'node:fs/promises';
import { closeGuard, pageQuestion } from './closing.ts';
import { join } from 'node:path';
import { runCollect, type CollectJob } from './collect.ts';
import { displayCommand } from './launch.ts';
import { ParserWorker } from './worker.ts';
import type { Event } from './protocol.ts';

const appDir = join(__dirname, '..');
let win: BrowserWindow | null = null;
let worker: ParserWorker | null = null;

function libraryRoot(): string | undefined {
  return process.env['LITRAG_ROOT'] || process.env['PROTRACKER_LIBRARY'] || undefined;
}

function forward(event: Event): void {
  win?.webContents.send('worker:event', event);
}

function startWorker(): ParserWorker {
  // the command itself — LITRAG_PARSER, the installed environment, or uv in a checkout — is launch.ts's to decide
  const w = new ParserWorker({
    appDir,
    root: libraryRoot(),
    isPackaged: app.isPackaged,
    onEvent: forward,
    onExit: (code, tail) => {
      forward({ event: 'worker-exit', code, tail });
    },
  });
  w.start();
  return w;
}

/** The app was asked to quit, not only a window to close: a close let through goes on to quit. */
let quitting = false;

/** The window's page has said it answers the question asked on closing; one still loading has nothing to ask about. */
let asks = false;
ipcMain.on('app:closing-asks', () => {
  asks = true;
});

/** The question asked of the page on closing, and its answer — the page's, or "may close" when it can give none. */
const closing = pageQuestion();
ipcMain.on('app:close-answer', (_e, ok: boolean) => closing.answer(ok !== false));

/** `LITRAG_HEADLESS=1`: no window on any screen — hidden, rendered offscreen — for tests and CI. */
const headless = process.env['LITRAG_HEADLESS'] === '1';

function createWindow(): void {
  win = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 900,
    minHeight: 600,
    title: 'litrag',
    backgroundColor: '#f6f5f2',
    show: !headless,
    webPreferences: {
      preload: join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      offscreen: headless,
      // a hidden page's animation frames are paused by Chromium, and pdf.js paints on them
      backgroundThrottling: !headless,
      // Chromium's spellchecker fetches its dictionary from Google's CDN: a call invariant 1 does not allow
      spellcheck: false,
    },
  });
  asks = false;
  win.loadFile(join(__dirname, 'index.html'));
  const w = win;
  // what is not saved on the Plots tab's picture is asked about before the window goes, as before anything else replaces it
  const guard = closeGuard(async () => {
    if (!asks || w.webContents.isDestroyed() || w.webContents.isCrashed()) return true;
    const ok = await closing.ask(() => w.webContents.send('app:closing'));
    if (!ok) quitting = false;
    return ok;
  });
  // a page that is gone, hangs or is loading again will not answer: the question waiting is settled, and none is put to it until it says it answers
  const cannotAnswer = (): void => {
    asks = false;
    closing.answer(true);
  };
  w.webContents.on('render-process-gone', cannotAnswer);
  w.webContents.on('did-start-navigation', (details) => {
    if (details.isMainFrame && !details.isSameDocument) cannotAnswer();
  });
  w.on('unresponsive', () => closing.answer(true));
  win.on('close', (e) => guard(() => e.preventDefault(), () => (quitting ? app.quit() : w.close())));
  win.on('closed', () => {
    win = null;
  });
}

ipcMain.handle('worker:request', async (_e, op: string, params: Record<string, unknown>) => {
  if (!worker) worker = startWorker();
  return worker.request(op, params);
});

ipcMain.handle('dialog:pdfs', async () => {
  if (!win) return [];
  const r = await dialog.showOpenDialog(win, {
    title: 'Papers to ingest',
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'Papers', extensions: ['pdf', 'xml'] }],
  });
  return r.canceled ? [] : r.filePaths;
});

ipcMain.handle('dialog:image', async () => {
  if (!win) return null;
  const r = await dialog.showOpenDialog(win, {
    title: 'An image of a plot to digitise',
    properties: ['openFile'],
    filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'webp'] }],
  });
  return r.canceled ? null : (r.filePaths[0] ?? null);
});

ipcMain.handle('dialog:choose', async (_e, message: string, choices: string[]) => {
  if (!win) return choices.length - 1;
  const r = await dialog.showMessageBox(win, { type: 'question', message: String(message), buttons: choices.map(String), defaultId: choices.length - 1, cancelId: choices.length - 1, noLink: true });
  return r.response;
});

ipcMain.handle('dialog:save', async (_e, defaultName: string) => {
  if (!win) return null;
  const r = await dialog.showSaveDialog(win, {
    title: 'Export the points',
    defaultPath: String(defaultName || 'points.csv'),
    filters: [{ name: 'CSV', extensions: ['csv'] }],
  });
  return r.canceled || !r.filePath ? null : r.filePath;
});

ipcMain.handle('file:read', async (_e, path: string) => {
  const buf = await readFile(path);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
});

ipcMain.handle('collect:run', async (_e, job: CollectJob) => runCollect(job, (event) => forward(event)));

ipcMain.handle('shell:open', async (_e, url: string) => {
  // only a web page, and only when a person clicked a link: nothing else leaves the machine this way
  if (/^https?:\/\//i.test(String(url))) await shell.openExternal(String(url));
});

ipcMain.handle('app:info', async () => {
  if (!worker) worker = startWorker();
  return { root: libraryRoot() ?? '~/.protracker/library', command: worker.command ? displayCommand(worker.command) : null, version: app.getVersion() };
});

app.whenReady().then(() => {
  // Chromium's spellchecker fetches a dictionary from Google's CDN (gvt1.com) for every new profile:
  // a call invariant 1 does not allow. Off in both sessions before any window, and its download
  // pointed at a port nothing listens on.
  for (const ses of [session.defaultSession, session.fromPartition('persist:litrag-collect')]) {
    ses.setSpellCheckerDictionaryDownloadURL('http://127.0.0.1:9/');
    ses.setSpellCheckerEnabled(false);
  }
  worker = startWorker();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  worker?.stop();
  if (process.platform !== 'darwin') app.quit();
});

// a quit may still be called off by the question a window asks on closing: the worker, which a Save needs, stops only once the app does quit
app.on('before-quit', () => {
  quitting = true;
});
app.on('will-quit', () => worker?.stop());
