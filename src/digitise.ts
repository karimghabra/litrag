/**
 * `lit digitise` and `lit fit`: the Plots tab's digitiser and fit from a shell. The work is the
 * parser's (parser/litrag_parser/digitise_cli.py) — the figure cut from its page, the axes read,
 * the series found by its colour, the sigmoid fitted — so these verbs hand their arguments to it
 * whole, through `bin/parser-run.js`, and pass its output and exit code back. Unlike the rest of
 * this CLI they read the tree store, not `lit.sqlite`.
 */

import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const VERBS = new Set(['digitise', 'digitize', 'fit']);

/** The parser's argv for a `lit` command line whose verb is digitise or fit, or null for any other:
 *  everything after the verb as it was typed, with `--root` and `--json` given before the verb carried over. */
export function delegated(argv: string[]): string[] | null {
  let root: string | undefined;
  let json = false;
  let i = 0;
  for (; i < argv.length && argv[i]!.startsWith('--'); i++) {
    const flag = argv[i]!;
    const value = argv[i + 1];
    if (flag === '--json') json = true;
    else if (value !== undefined && !value.startsWith('--')) {
      if (flag === '--root') root = value;
      i += 1; // a global flag's value, not the verb
    }
  }
  const verb = argv[i];
  if (verb === undefined || !VERBS.has(verb)) return null;
  const rest = argv.slice(i + 1);
  const carry = [...(root !== undefined && !rest.includes('--root') ? ['--root', root] : []), ...(json && !rest.includes('--json') ? ['--json'] : [])];
  return [verb === 'digitize' ? 'digitise' : verb, ...rest, ...carry];
}

/** Runs the parser's digitiser with `args`; its exit code. */
export function runDigitiser(args: string[]): number {
  const runner = join(dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'parser-run.js');
  const r = spawnSync(process.execPath, [runner, 'python', '-m', 'litrag_parser.digitise_cli', ...args], { stdio: 'inherit' });
  if (r.error) {
    process.stderr.write(`lit ${args[0]}: ${r.error.message}\n`);
    return 1;
  }
  return r.status ?? 1;
}
