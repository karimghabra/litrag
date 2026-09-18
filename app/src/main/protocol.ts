/**
 * The wire between the app and the parsing worker: JSON lines, one request
 * per line in, one or more events per line out, matched by id. This module
 * is pure — the framing and the correlation — so it can be tested without a
 * process; `worker.ts` owns the spawn.
 */

export interface Request {
  id: string;
  op: string;
  [key: string]: unknown;
}

export interface Event {
  event: string;
  id?: string;
  [key: string]: unknown;
}

/** Splits a byte stream into complete lines, holding a partial line until the rest arrives. */
export class LineSplitter {
  private rest = '';
  push(chunk: string): string[] {
    const text = this.rest + chunk;
    const parts = text.split('\n');
    this.rest = parts.pop() ?? '';
    return parts.map((p) => p.trim()).filter(Boolean);
  }
  flush(): string[] {
    const r = this.rest.trim();
    this.rest = '';
    return r ? [r] : [];
  }
}

export function parseEvent(line: string): Event | null {
  try {
    const value = JSON.parse(line) as unknown;
    if (value && typeof value === 'object' && typeof (value as Event).event === 'string') return value as Event;
    return null;
  } catch {
    return null;
  }
}

/** Events that close a request: the answer to a read, or the end of an ingest. */
export const TERMINAL = new Set(['done', 'error', 'hello', 'libraries', 'library', 'papers', 'node', 'section', 'events', 'rows', 'file', 'bye', 'queued', 'refs', 'audit', 'edges']);

/** `tree` answers a `tree` request and also streams during ingest; only the former closes a request. */
export function closesRequest(event: Event, op: string | undefined): boolean {
  if (TERMINAL.has(event.event)) return true;
  if (event.event === 'tree' && (op === 'tree' || op === 'parse_json')) return true;
  if (event.event === 'audit' && op === 'audit') return true;
  return false;
}

let counter = 0;
export function nextId(prefix = 'r'): string {
  counter += 1;
  return `${prefix}${counter}`;
}
