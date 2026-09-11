/**
 * The parsing worker as a child process.
 *
 * Spawned once, kept for the life of the window: `uv run litrag-parser` in the
 * repository's `parser/` folder by default, or whatever `LITRAG_PARSER` names.
 * Requests get an id; the promise resolves on the event that closes it, and
 * every event — closing or not — is handed to `onEvent` so the renderer can
 * show stages as they happen.
 */

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { closesRequest, LineSplitter, nextId, parseEvent, type Event, type Request } from './protocol.ts';

export interface WorkerOptions {
  /** Where the libraries live; passed through as --root. */
  root?: string;
  /** Override the command: e.g. "uv run --project /x/parser litrag-parser". */
  command?: string;
  appDir: string;
  onEvent: (event: Event) => void;
  onExit: (code: number | null, stderrTail: string) => void;
}

interface Pending {
  op: string;
  resolve: (e: Event) => void;
  reject: (err: Error) => void;
}

export function defaultCommand(appDir: string, env: NodeJS.ProcessEnv = process.env): { cmd: string; args: string[] } {
  if (env['LITRAG_PARSER']) {
    const parts = env['LITRAG_PARSER'].split(/\s+/).filter(Boolean);
    return { cmd: parts[0] ?? 'uv', args: parts.slice(1) };
  }
  const parserDir = resolve(appDir, '..', 'parser');
  if (existsSync(join(parserDir, 'pyproject.toml'))) {
    return { cmd: 'uv', args: ['run', '--project', parserDir, 'litrag-parser'] };
  }
  return { cmd: 'litrag-parser', args: [] };
}

export class ParserWorker {
  private child: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, Pending>();
  private stderrTail: string[] = [];
  readonly command: { cmd: string; args: string[] };

  constructor(private readonly options: WorkerOptions) {
    this.command = defaultCommand(options.appDir);
    if (options.command) {
      const parts = options.command.split(/\s+/).filter(Boolean);
      this.command = { cmd: parts[0] ?? 'uv', args: parts.slice(1) };
    }
  }

  start(): void {
    const args = [...this.command.args];
    if (this.options.root) args.push(`--root=${this.options.root}`);
    const child = spawn(this.command.cmd, args, { stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONUNBUFFERED: '1' } });
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
    child.on('error', (err) => {
      this.options.onEvent({ event: 'worker-error', message: `${this.command.cmd}: ${err.message}` });
    });
    child.on('exit', (code) => {
      for (const p of this.pending.values()) p.reject(new Error(`worker exited (${code})`));
      this.pending.clear();
      this.child = null;
      this.options.onExit(code, this.stderrTail.slice(-20).join('\n'));
    });
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
    if (!child) return Promise.reject(new Error('worker is not running'));
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
