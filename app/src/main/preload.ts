/**
 * The bridge the renderer sees: a handful of calls into the main process and
 * one event stream out of it. Context isolation is on; nothing else of Node
 * or Electron reaches the page.
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron';

export interface LitragApi {
  request(op: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
  onEvent(handler: (event: Record<string, unknown>) => void): () => void;
  choosePdfs(): Promise<string[]>;
  pathsOf(files: File[]): string[];
  readFile(path: string): Promise<ArrayBuffer>;
  info(): Promise<{ root: string; command: string; version: string }>;
}

const api: LitragApi = {
  request: (op, params = {}) => ipcRenderer.invoke('worker:request', op, params),
  onEvent: (handler) => {
    const listener = (_: unknown, event: Record<string, unknown>) => handler(event);
    ipcRenderer.on('worker:event', listener);
    return () => ipcRenderer.removeListener('worker:event', listener);
  },
  choosePdfs: () => ipcRenderer.invoke('dialog:pdfs'),
  pathsOf: (files) => files.map((f) => webUtils.getPathForFile(f)),
  readFile: (path) => ipcRenderer.invoke('file:read', path),
  info: () => ipcRenderer.invoke('app:info'),
};

contextBridge.exposeInMainWorld('litrag', api);
