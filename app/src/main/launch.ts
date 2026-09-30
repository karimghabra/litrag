/**
 * How the window starts its worker: which program, with which arguments.
 *
 * Three ways, first match wins:
 *
 *  1. An override — `options.command`, else `LITRAG_PARSER` — in one of three forms:
 *     a JSON array of argv (`["C:\\Users\\Jane Doe\\…\\litrag-parser.exe", "--flag"]`), a path to
 *     an existing file (spaces and all, taken as one program with no arguments), or the old
 *     whitespace-split command line (`uv run --project /x/parser litrag-parser`).
 *  2. An installed app (`app.isPackaged`): the parser's console script in the environment the
 *     installer made — `<venv>\Scripts\litrag-parser.exe` on Windows, `<venv>/bin/litrag-parser`
 *     elsewhere, `<venv>` being `$LITRAG_VENV` or `<install root>/venv`. uv is not run; a
 *     missing environment is an error that says how to install it, and nothing is spawned. This
 *     comes before the checkout test on purpose: an installed app has `resources/parser` with its
 *     `pyproject.toml` right where a checkout's `parser/` would be.
 *  3. A checkout (`parser/pyproject.toml` beside the app): `uv run --project <repo>/parser
 *     litrag-parser`, uv found on PATH or where its installers put it.
 *
 * Pure: every fact about the machine comes in through `LaunchContext`, and Windows paths are
 * built with `path.win32` whatever the host, so every case is a test on any machine.
 */

import { posix, win32 } from 'node:path';

export interface LaunchContext {
  existsSync: (path: string) => boolean;
  env: Record<string, string | undefined>;
  platform: NodeJS.Platform;
  homedir: string;
  /** Electron's `app.isPackaged`. The installed app's `resources/parser` is the installer's to build
   *  the environment from; the app itself never reads it, so `process.resourcesPath` is not asked. */
  isPackaged: boolean;
  /** The app's folder: `app/` in a checkout (the one above `dist/`), `resources/app.asar` installed. */
  appDir: string;
  /** An explicit command, as `LITRAG_PARSER` would give it; wins over the variable. */
  command?: string | undefined;
}

export interface Command {
  cmd: string;
  args: string[];
}

export type Launch = Command | { error: string };

/** The program the worker runs as, or why there is none to run. */
export function resolveCommand(ctx: LaunchContext): Launch {
  const p = pathsFor(ctx.platform);
  const override = ctx.command?.trim() ? ctx.command : envOf(ctx, 'LITRAG_PARSER');
  if (override?.trim()) return parseCommand(override, ctx.existsSync, ctx.command?.trim() ? 'the worker command' : 'LITRAG_PARSER');

  if (ctx.isPackaged) {
    const exe = parserExecutable(ctx);
    if (ctx.existsSync(exe)) return { cmd: exe, args: [] };
    const how = ctx.platform === 'win32' ? 'Run install.cmd from the litrag download' : 'Run the install script from the litrag download';
    return { error: `litrag's Python environment isn't installed (looked for ${exe}). ${how} to set it up, then start litrag again.` };
  }

  const parserDir = p.resolve(ctx.appDir, '..', 'parser');
  if (ctx.existsSync(p.join(parserDir, 'pyproject.toml'))) {
    const uv = findUv(ctx);
    if ('error' in uv) return uv;
    return { cmd: uv.path, args: ['run', '--project', parserDir, 'litrag-parser'] };
  }
  // neither installed nor a checkout: whatever `litrag-parser` is on PATH, as before
  return { cmd: 'litrag-parser', args: [] };
}

/**
 * An override, read the way it was most likely meant: a JSON array is argv exactly; a value that
 * is a file (once any quotes a file manager put round it are gone) is one program; anything else
 * is split on whitespace, the form `LITRAG_PARSER` has always taken.
 */
export function parseCommand(value: string, existsSync: (path: string) => boolean, source = 'LITRAG_PARSER'): Launch {
  const text = value.trim();
  if (text.startsWith('[')) {
    let argv: unknown;
    try {
      argv = JSON.parse(text);
    } catch (e) {
      return { error: `${source} starts with "[" but is not JSON (${(e as Error).message}): give an array of strings, e.g. ["C:\\\\path with spaces\\\\litrag-parser.exe"]` };
    }
    if (!Array.isArray(argv) || argv.length === 0 || !argv.every((a) => typeof a === 'string') || !argv[0]) {
      return { error: `${source} must be a non-empty JSON array of strings, the program first: got ${text}` };
    }
    const [cmd, ...args] = argv as string[];
    return { cmd: cmd!, args };
  }
  const unquoted = /^"[^"]+"$/.test(text) ? text.slice(1, -1) : text;
  if (existsSync(unquoted)) return { cmd: unquoted, args: [] };
  const parts = text.split(/\s+/).filter(Boolean);
  return { cmd: parts[0]!, args: parts.slice(1) };
}

/** Where an installed litrag lives: `app/`, `venv/`, `uv/` side by side. */
export function installRoot(ctx: Pick<LaunchContext, 'env' | 'platform' | 'homedir'>): string {
  const p = pathsFor(ctx.platform);
  if (ctx.platform === 'win32') return p.join(envOf(ctx, 'LOCALAPPDATA') || p.join(ctx.homedir, 'AppData', 'Local'), 'litrag');
  if (ctx.platform === 'darwin') return p.join(ctx.homedir, 'Library', 'Application Support', 'litrag');
  const xdg = envOf(ctx, 'XDG_DATA_HOME');
  return p.join(xdg && p.isAbsolute(xdg) ? xdg : p.join(ctx.homedir, '.local', 'share'), 'litrag');
}

/** The installed parser's console script: in `$LITRAG_VENV`, else in the install root's `venv`. */
export function parserExecutable(ctx: Pick<LaunchContext, 'env' | 'platform' | 'homedir'>): string {
  const p = pathsFor(ctx.platform);
  const venv = envOf(ctx, 'LITRAG_VENV') || p.join(installRoot(ctx), 'venv');
  return ctx.platform === 'win32' ? p.join(venv, 'Scripts', 'litrag-parser.exe') : p.join(venv, 'bin', 'litrag-parser');
}

/**
 * uv, by absolute path: on PATH first (with PATHEXT on Windows), then where litrag's installer
 * puts it, then where uv's own installers do. A window started from a desktop shortcut often has
 * a PATH without the shell profile's additions, which is why the fixed places are asked at all.
 */
export function findUv(ctx: Pick<LaunchContext, 'existsSync' | 'env' | 'platform' | 'homedir'>): { path: string } | { error: string } {
  const p = pathsFor(ctx.platform);
  const win = ctx.platform === 'win32';
  const dirs = (envOf(ctx, 'PATH') ?? '')
    .split(p.delimiter)
    .map((d) => (win ? d.replace(/^"(.*)"$/, '$1') : d).trim())
    .filter(Boolean);
  // Node will not spawn a .bat or .cmd without a shell, so only what it can run directly counts
  const exts = win
    ? (envOf(ctx, 'PATHEXT') || '.COM;.EXE;.BAT;.CMD')
        .split(';')
        .map((e) => e.trim())
        .filter((e) => /^\.(exe|com)$/i.test(e))
    : [''];
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = p.join(dir, `uv${ext.toLowerCase()}`);
      if (ctx.existsSync(candidate)) return { path: candidate };
    }
  }
  const fixed = win
    ? [p.join(installRoot(ctx), 'uv', 'uv.exe'), p.join(envOf(ctx, 'USERPROFILE') || ctx.homedir, '.local', 'bin', 'uv.exe')]
    : [p.join(installRoot(ctx), 'uv', 'uv'), p.join(ctx.homedir, '.local', 'bin', 'uv'), p.join(ctx.homedir, '.cargo', 'bin', 'uv')];
  for (const candidate of fixed) {
    if (ctx.existsSync(candidate)) return { path: candidate };
  }
  return {
    error:
      "uv isn't installed, or isn't where litrag looks for it, and a checkout runs its parser under uv. " +
      'Install it from https://docs.astral.sh/uv/getting-started/installation/ and start litrag again, ' +
      `or set LITRAG_PARSER to the worker's command. (Looked on PATH, then for ${fixed.join(', ')}.)`,
  };
}

/** A command as a person would type it, each part with spaces quoted: for the log, not for a shell. */
export function displayCommand(c: Command): string {
  return [c.cmd, ...c.args].map((a) => (/[\s"]/.test(a) || a === '' ? JSON.stringify(a) : a)).join(' ');
}

function pathsFor(platform: NodeJS.Platform): typeof posix {
  return platform === 'win32' ? win32 : posix;
}

/** An environment variable; on Windows its name is case-blind (`Path` is `PATH`), as there. */
function envOf(ctx: Pick<LaunchContext, 'env' | 'platform'>, name: string): string | undefined {
  const direct = ctx.env[name];
  if (direct !== undefined || ctx.platform !== 'win32') return direct;
  const key = Object.keys(ctx.env).find((k) => k.toUpperCase() === name);
  return key === undefined ? undefined : ctx.env[key];
}
