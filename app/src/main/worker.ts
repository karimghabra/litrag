/**
 * The parsing worker as a child process.
 *
 * Spawned once, kept for the life of the window. Which program it is — the installed
 * environment's `litrag-parser`, `uv run` in a checkout's `parser/`, or whatever `LITRAG_PARSER`
 * names — is `resolveCommand`'s answer (launch.ts). When there is nothing to run, nothing is
 * spawned: the reason is `problem`, it goes out as a `worker-error`, and every request rejects
 * with it. Requests get an id; the promise resolves on the event that closes it, and every event
 * — closing or not — is handed to `onEvent` so the renderer can show stages as they happen.
 */

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { displayCommand, resolveCommand, type Command, type Launch } from './launch.ts';
import { closesRequest, LineSplitter, nextId, parseEvent, type Event, type Request } from './protocol.ts';

export interface WorkerOptions {
  /** Where the libraries live; passed through as --root. */
  root?: string | undefined;
  /** Override the command, in any form `LITRAG_PARSER` takes (launch.ts); wins over the variable. */
  command?: string | undefined;
  appDir: string;
  /** Electron's `app.isPackaged`: an installed app runs its environment's console script, never uv. */
  isPackaged?: boolean | undefined;
  /** The environment the command is resolved in and the worker runs in; `process.env` by default. */
  env?: NodeJS.ProcessEnv | undefined;
  onEvent: (event: Event) => void;
  onExit: (code: number | null, stderrTail: string) => void;
}

interface Pending {
  op: string;
  resolve: (e: Event) => void;
  reject: (err: Error) => void;
}

/** The worker's command on this machine: `resolveCommand` over the real file system. */
export function defaultCommand(appDir: string, env: NodeJS.ProcessEnv = process.env, more: { isPackaged?: boolean | undefined; command?: string | undefined } = {}): Launch {
  return resolveCommand({ existsSync, env, platform: process.platform, homedir: homedir(), appDir, isPackaged: more.isPackaged ?? false, command: more.command });
}

export class ParserWorker {
  private child: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, Pending>();
  private stderrTail: string[] = [];
  private readonly env: NodeJS.ProcessEnv;
  /** What runs as the worker; null when there is nothing to run, and `problem` says why. */
  readonly command: Command | null;
  /** Why the worker is not running, in words for the window: nothing to run, it would not start, or it exited. */
  problem: string | null = null;

  constructor(private readonly options: WorkerOptions) {
    this.env = options.env ?? process.env;
    const launch = defaultCommand(options.appDir, this.env, options);
    if ('error' in launch) {
      this.command = null;
      this.problem = launch.error;
    } else {
      this.command = launch;
    }
  }

  start(): void {
    const command = this.command;
    if (!command) {
      this.options.onEvent({ event: 'worker-error', message: this.problem });
      return;
    }
    this.problem = null;
    const args = [...command.args];
    if (this.options.root) args.push(`--root=${this.options.root}`);
    // windowsHide: an installed app has no console, and Windows would open one for the worker
    const child = spawn(command.cmd, args, { stdio: ['pipe', 'pipe', 'pipe'], env: { ...this.env, PYTHONUNBUFFERED: '1', PYTHONUTF8: '1' }, windowsHide: true });
    this.child = child;
    const lines = new LineSplitter();
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => {
      for (const line of lines.push(chunk)) this.handle(line);
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk: string) => {
      for (const line of chunk.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        this.stderrTail.push(t);
        if (this.stderrTail.length > 200) this.stderrTail.shift();
        this.options.onEvent({ event: 'stderr', message: t });
      }
    });
    // a write to a worker that has just died: its exit says what happened, not this
    child.stdin.on('error', () => undefined);
    child.on('error', (err) => {
      const message = `The worker would not start (${displayCommand(command)}): ${err.message}`;
      if (child.pid === undefined) {
        // never ran, so no exit follows: whatever waits on it is answered here
        this.problem = message;
        this.child = null;
        this.rejectAll(message);
      }
      this.options.onEvent({ event: 'worker-error', message });
    });
    child.on('exit', (code) => {
      const tail = this.stderrTail.slice(-20).join('\n');
      this.problem = `The worker exited (${code})${tail ? `; its last lines:\n${tail}` : ''}`;
      this.rejectAll(`worker exited (${code})`);
      this.child = null;
      this.options.onExit(code, tail);
    });
  }

  private rejectAll(message: string): void {
    for (const p of this.pending.values()) p.reject(new Error(message));
    this.pending.clear();
  }

  private handle(line: string): void {
    const event = parseEvent(line);
    if (!event) {
      this.options.onEvent({ event: 'stdout', message: line });
      return;
    }
    this.options.onEvent(event);
    if (event.id && this.pending.has(event.id)) {
      const p = this.pending.get(event.id)!;
      if (closesRequest(event, p.op)) {
        this.pending.delete(event.id);
        if (event.event === 'error') p.reject(new Error(String(event['message'] ?? 'worker error')));
        else p.resolve(event);
      }
    }
  }

  request(op: string, params: Record<string, unknown> = {}): Promise<Event> {
    const child = this.child;
    if (!child) return Promise.reject(new Error(this.problem ?? 'worker is not running'));
    const id = nextId();
    const req: Request = { id, op, ...params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { op, resolve, reject });
      child.stdin.write(`${JSON.stringify(req)}\n`);
    });
  }

  stop(): void {
    const child = this.child;
    if (!child) return;
    try {
      child.stdin.write(`${JSON.stringify({ id: nextId('q'), op: 'quit' })}\n`);
    } catch {
      /* already gone */
    }
    setTimeout(() => child.kill(), 1500).unref();
  }
}
