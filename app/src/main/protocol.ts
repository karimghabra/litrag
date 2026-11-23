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
export const TERMINAL = new Set([
  'done', 'error', 'hello', 'libraries', 'library', 'papers', 'node', 'section', 'events', 'rows', 'file', 'bye', 'queued', 'refs', 'audit', 'edges',
  // the studio's reads: projects, a search and its candidates, the types and a paper's mapping, retrieval
  'projects', 'project', 'search', 'candidates', 'wanted', 'dismissed', 'suggestions', 'types', 'mapping', 'retrieval', 'query',
  // the truth for the finding→method links: the queue, a finding labelled, every label, the measure
  'label_queue', 'labelled', 'labels', 'truth',
  // the numbers read from a figure, or every figure of a paper
  'charts',
  // the works and the citations between them, and the passages that cite one
  'graph', 'passages',
  // the shared store: a move's plan, a project deleted, papers a project takes or lets go
  'migrate', 'deleted', 'held', 'let_go',
  // a cited answer from the local model, and the models it can be asked of
  'answer', 'models',
  // accounts, and each person's recent papers, favourites, notes and collections
  'users', 'signed_in', 'signed_out', 'password', 'viewed', 'recent', 'favourite', 'favourites', 'notes', 'note', 'note_deleted',
  'collections', 'collection', 'collection_deleted', 'collection_changed', 'collection_papers', 'paper_marks',
  // the digitiser: plots found, a figure opened, a series found, saved, exported, fitted
  'plots', 'digitise', 'axes', 'extracted', 'digitised', 'exported', 'fit',
]);

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
