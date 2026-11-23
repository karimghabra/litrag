/**
 * `lit ask`: a cited answer from the local model, over the tree store.
 *
 * The first of the CLI's verbs to read the tree rather than `lit.sqlite`: it starts the same
 * Python worker the window starts (`app/src/main/launch.ts` decides which program), sends it one
 * `ask` request on the JSON-lines wire, prints its `asking` events to stderr as they come, and
 * hands back its `answer`. Nothing here reads SQLite or talks to a model; the worker does both
 * (`parser/litrag_parser/answer.py`).
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { resolveCommand, type Command } from '../app/src/main/launch.ts';

export interface AskSource {
  n: number;
  lib: string;
  node_id: string;
  paper: string;
  title?: string | null;
  year?: string | null;
  doi?: string | null;
  section?: string | null;
  ancestry?: string[];
  page?: number | null;
  text: string;
  cited?: boolean;
}

export interface AskAnswer {
  question: string;
  libs: string[];
  model: string;
  status: 'answered' | 'partial' | 'not_found' | 'unanswered';
  summary: string;
  claims: { text: string; sources: number[]; support: 'supported' | 'weak'; numbers_missing?: string[] }[];
  dropped: { text: string; reason: string }[];
  missing: string;
  sources: AskSource[];
  timing?: { total?: number; tokens_per_second?: number | null };
  error?: string;
  warning?: string;
}

export interface AskParams {
  libs: string[];
  question: string;
  model?: string;
  k?: number;
}

/** The worker's command for this checkout, as the window would start it. */
export function workerCommand(env: Record<string, string | undefined> = process.env): Command {
  const appDir = fileURLToPath(new URL('../app', import.meta.url));
  const launch = resolveCommand({ existsSync, env, platform: process.platform, homedir: homedir(), isPackaged: false, appDir });
  if ('error' in launch) throw new Error(launch.error);
  return launch;
}

/** The request line `lit ask` sends. */
export function askRequest(id: string, p: AskParams): Record<string, unknown> {
  return { id, op: 'ask', libs: p.libs, question: p.question, ...(p.model ? { model: p.model } : {}), ...(p.k !== undefined ? { k: p.k } : {}) };
}

/**
 * One question to a worker started for it: resolves with the `answer`, rejects with an `error`
 * event's message or with why the worker ended without one. `onEvent` hears the `asking`
 * progress events. The worker is told to quit once the answer is in.
 */
export function askWorker(command: Command, root: string, p: AskParams, onEvent: (ev: Record<string, unknown>) => void = () => undefined): Promise<AskAnswer> {
  return new Promise((resolve, reject) => {
    const child = spawn(command.cmd, [...command.args, `--root=${root}`], { stdio: ['pipe', 'pipe', 'pipe'] });
    const id = 'ask1';
    let rest = '';
    let err = '';
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      fn();
      child.stdin.end(`${JSON.stringify({ id: 'quit', op: 'quit' })}\n`);
    };
    child.stdin.on('error', () => undefined); // a worker gone before the quit line is no failure of the answer
    child.on('error', (e) => finish(() => reject(new Error(`The worker would not start (${command.cmd}): ${e.message}`))));
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (d: string) => {
      err = (err + d).slice(-4000);
    });
    child.stdout.on('data', (d: string) => {
      const lines = (rest + d).split('\n');
      rest = lines.pop() ?? '';
      for (const line of lines) {
        let ev: Record<string, unknown>;
        try {
          ev = JSON.parse(line) as Record<string, unknown>;
        } catch {
          continue;
        }
        if (ev['event'] === 'ready') child.stdin.write(`${JSON.stringify(askRequest(id, p))}\n`);
        if (ev['id'] !== id) continue;
        if (ev['event'] === 'asking') onEvent(ev);
        else if (ev['event'] === 'answer') finish(() => resolve(ev as unknown as AskAnswer));
        else if (ev['event'] === 'error') finish(() => reject(new Error(String(ev['message'] ?? 'the worker failed'))));
      }
    });
    child.on('close', (code) => finish(() => reject(new Error(`The worker ended (exit ${code}) without an answer${err.trim() ? `: ${err.trim().split('\n').slice(-3).join(' ')}` : ''}`))));
  });
}

/** A passage's place: paper (year) — section path — page. */
export function where(s: AskSource): string {
  const path = (s.ancestry ?? []).filter(Boolean).join(' › ') || s.section || '';
  return [`${s.title || s.paper}${s.year ? ` (${s.year})` : ''}`, path, s.page ? `p. ${s.page}` : ''].filter(Boolean).join(' — ');
}

/** The answer for a person: the cited prose, what to check, what was left out, the passages. */
export function formatAnswer(a: AskAnswer): string {
  const out: string[] = [`${a.status} · ${a.model}${a.timing?.total !== undefined ? ` · ${a.timing.total} s` : ''}`, '', a.summary];
  if (a.warning) out.push('', a.warning);
  if (a.missing && a.status === 'partial') out.push('', `Not in the passages: ${a.missing}`);
  const weak = a.claims.filter((c) => c.support === 'weak');
  if (weak.length) {
    out.push('', 'Check these against their passages (a number or most words not found there):');
    for (const c of weak) out.push(`  - ${c.text} [${c.sources.join(', ')}]${c.numbers_missing?.length ? ` · not in the passage: ${c.numbers_missing.join(', ')}` : ''}`);
  }
  if (a.dropped.length) out.push('', `${a.dropped.length} sentence${a.dropped.length === 1 ? '' : 's'} left out: ${a.dropped.length === 1 ? 'it cited' : 'they cited'} no passage given.`);
  if (a.sources.length) out.push('');
  for (const s of a.sources) {
    out.push(`${s.cited ? '*' : ' '}[${s.n}] ${where(s)}${a.libs.length > 1 ? `  [${s.lib}]` : ''}`);
    out.push(`      ${s.doi ? `doi:${s.doi} · ` : ''}${s.node_id}`);
  }
  return out.join('\n');
}
