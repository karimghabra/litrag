/**
 * Electron's main process: one window, one parsing worker, and the IPC
 * between them. The renderer never touches the file system or the worker
 * directly; it asks here, and every worker event is forwarded to it.
 */

import { app, BrowserWindow, dialog, ipcMain } from 'electron';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
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
  const w = new ParserWorker({
    appDir,
    root: libraryRoot(),
    command: process.env['LITRAG_PARSER'],
    onEvent: forward,
    onExit: (code, tail) => {
      forward({ event: 'worker-exit', code, tail });
    },
  });
  w.start();
  return w;
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 900,
    minHeight: 600,
    title: 'litrag',
    backgroundColor: '#f6f5f2',
    webPreferences: {
      preload: join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.loadFile(join(__dirname, 'index.html'));
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

ipcMain.handle('file:read', async (_e, path: string) => {
  const buf = await readFile(path);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
});

ipcMain.handle('app:info', async () => {
  if (!worker) worker = startWorker();
  return { root: libraryRoot() ?? '~/.protracker/library', command: [worker.command.cmd, ...worker.command.args].join(' '), version: app.getVersion() };
});

app.whenReady().then(() => {
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

app.on('before-quit', () => worker?.stop());
