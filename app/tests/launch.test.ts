import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { displayCommand, parseCommand, resolveCommand, type LaunchContext } from '../src/main/launch.ts';
import { ParserWorker } from '../src/main/worker.ts';

/** A machine that holds exactly these files. */
const machine = (files: string[], over: Partial<LaunchContext> = {}): LaunchContext => {
  const held = new Set(files);
  return { existsSync: (p) => held.has(p), env: {}, platform: 'linux', homedir: '/home/jane', isPackaged: false, appDir: '/src/litrag/app', ...over };
};
const REPO_PARSER = '/src/litrag/parser/pyproject.toml';
const devArgs = ['run', '--project', '/src/litrag/parser', 'litrag-parser'];

describe('a checkout: uv run in the repository parser', () => {
  it('takes uv from PATH, by its absolute path', () => {
    const m = machine([REPO_PARSER, '/opt/uv/bin/uv'], { env: { PATH: '/usr/bin:/opt/uv/bin' } });
    expect(resolveCommand(m)).toEqual({ cmd: '/opt/uv/bin/uv', args: devArgs });
  });

  it('finds uv in ~/.local/bin when PATH does not have it, as from a desktop shortcut', () => {
    const m = machine([REPO_PARSER, '/home/jane/.local/bin/uv'], { env: { PATH: '/usr/bin:/bin' } });
    expect(resolveCommand(m)).toEqual({ cmd: '/home/jane/.local/bin/uv', args: devArgs });
    const cargo = machine([REPO_PARSER, '/home/jane/.cargo/bin/uv'], { env: {} });
    expect(resolveCommand(cargo)).toEqual({ cmd: '/home/jane/.cargo/bin/uv', args: devArgs });
  });

  it('runs an environment that exists as it was synced, so a cu130 torch is not synced back to PyPI', () => {
    const m = machine([REPO_PARSER, '/opt/uv/bin/uv', '/src/litrag/parser/.venv'], { env: { PATH: '/opt/uv/bin' } });
    expect(resolveCommand(m)).toEqual({ cmd: '/opt/uv/bin/uv', args: ['run', '--project', '/src/litrag/parser', '--no-sync', 'litrag-parser'] });
    // UV_PROJECT_ENVIRONMENT, relative to the project as uv reads it, is the environment asked about
    const moved = machine([REPO_PARSER, '/opt/uv/bin/uv', '/src/litrag/envs/gpu'], { env: { PATH: '/opt/uv/bin', UV_PROJECT_ENVIRONMENT: '../envs/gpu' } });
    expect(resolveCommand(moved)).toEqual({ cmd: '/opt/uv/bin/uv', args: ['run', '--project', '/src/litrag/parser', '--no-sync', 'litrag-parser'] });
  });

  it('says uv is missing and how to get it, rather than spawning a name that is not there', () => {
    const r = resolveCommand(machine([REPO_PARSER], { env: { PATH: '/usr/bin' } }));
    expect(r).toHaveProperty('error');
    const error = (r as { error: string }).error;
    expect(error).toMatch(/^uv isn't installed/);
    expect(error).toContain('/home/jane/.local/bin/uv');
    expect(error).toContain('https://docs.astral.sh/uv/');
    expect(error).toContain('LITRAG_PARSER');
  });

  it('on Windows honours PATHEXT, a PATH spelt Path, quoted entries and spaces, then litrag\'s own uv', () => {
    const repo = 'C:\\Users\\Jane Doe\\code\\litrag';
    const base = { platform: 'win32' as const, homedir: 'C:\\Users\\Jane Doe', appDir: `${repo}\\app` };
    const parser = `${repo}\\parser\\pyproject.toml`;
    const onPath = machine([parser, 'C:\\Program Files\\uv\\uv.exe'], { ...base, env: { Path: 'C:\\Windows;"C:\\Program Files\\uv"', PATHEXT: '.COM;.EXE;.BAT;.CMD' } });
    expect(resolveCommand(onPath)).toEqual({ cmd: 'C:\\Program Files\\uv\\uv.exe', args: ['run', '--project', `${repo}\\parser`, 'litrag-parser'] });
    // a .cmd shim is not something Node spawns without a shell: passed over
    const shim = machine([parser, 'C:\\shims\\uv.cmd'], { ...base, env: { PATH: 'C:\\shims' } });
    expect(resolveCommand(shim)).toHaveProperty('error');
    const installed = 'C:\\Users\\Jane Doe\\AppData\\Local\\litrag\\uv\\uv.exe';
    const ours = machine([parser, installed], { ...base, env: { PATH: 'C:\\Windows', LOCALAPPDATA: 'C:\\Users\\Jane Doe\\AppData\\Local' } });
    expect(resolveCommand(ours)).toMatchObject({ cmd: installed });
    const local = 'C:\\Users\\Jane Doe\\.local\\bin\\uv.exe';
    const astral = machine([parser, local], { ...base, env: { USERPROFILE: 'C:\\Users\\Jane Doe' } });
    expect(resolveCommand(astral)).toMatchObject({ cmd: local });
  });

  it('neither installed nor a checkout: litrag-parser on PATH, as before', () => {
    expect(resolveCommand(machine([]))).toEqual({ cmd: 'litrag-parser', args: [] });
  });
});

describe('an installed app: the environment the installer made, never uv', () => {
  const winHome = 'C:\\Users\\Jane Doe';
  const localAppData = `${winHome}\\AppData\\Local`;
  const packagedWin = (files: string[], env: Record<string, string> = {}) =>
    machine(files, {
      platform: 'win32',
      homedir: winHome,
      isPackaged: true,
      appDir: `${localAppData}\\litrag\\app\\resources\\app.asar`,
      env: { LOCALAPPDATA: localAppData, PATH: 'C:\\Windows', ...env },
    });

  it('on Windows runs %LOCALAPPDATA%\\litrag\\venv\\Scripts\\litrag-parser.exe, the space in the user name kept whole', () => {
    const exe = `${localAppData}\\litrag\\venv\\Scripts\\litrag-parser.exe`;
    // resources\parser is there too, with its pyproject: the installed app does not take it for a checkout
    const m = packagedWin([exe, `${localAppData}\\litrag\\app\\resources\\parser\\pyproject.toml`, 'C:\\Windows\\uv.exe']);
    expect(resolveCommand(m)).toEqual({ cmd: exe, args: [] });
  });

  it('takes LITRAG_VENV over the install root', () => {
    const exe = 'D:\\envs\\lit rag\\Scripts\\litrag-parser.exe';
    expect(resolveCommand(packagedWin([exe], { LITRAG_VENV: 'D:\\envs\\lit rag' }))).toEqual({ cmd: exe, args: [] });
    const posixVenv = machine(['/opt/litrag env/bin/litrag-parser'], { isPackaged: true, env: { LITRAG_VENV: '/opt/litrag env' } });
    expect(resolveCommand(posixVenv)).toEqual({ cmd: '/opt/litrag env/bin/litrag-parser', args: [] });
  });

  it('with no environment, says where it looked and to run the installer, and spawns nothing', () => {
    const r = resolveCommand(packagedWin(['C:\\Windows\\uv.exe']));
    expect(r).toEqual({
      error: `litrag's Python environment isn't installed (looked for ${localAppData}\\litrag\\venv\\Scripts\\litrag-parser.exe). Run install.cmd from the litrag download to set it up, then start litrag again.`,
    });
  });

  it('finds the install root the way the installer does on Linux and macOS', () => {
    const linux = machine(['/home/jane/.local/share/litrag/venv/bin/litrag-parser'], { isPackaged: true });
    expect(resolveCommand(linux)).toEqual({ cmd: '/home/jane/.local/share/litrag/venv/bin/litrag-parser', args: [] });
    const xdg = machine(['/data/jane/litrag/venv/bin/litrag-parser'], { isPackaged: true, env: { XDG_DATA_HOME: '/data/jane' } });
    expect(resolveCommand(xdg)).toEqual({ cmd: '/data/jane/litrag/venv/bin/litrag-parser', args: [] });
    const mac = machine([], { isPackaged: true, platform: 'darwin', homedir: '/Users/jane' });
    expect((resolveCommand(mac) as { error: string }).error).toContain('/Users/jane/Library/Application Support/litrag/venv/bin/litrag-parser');
  });
});

describe('LITRAG_PARSER, and the command option', () => {
  const exe = 'C:\\Users\\Jane Doe\\AppData\\Local\\litrag\\venv\\Scripts\\litrag-parser.exe';

  it('takes a JSON array as argv exactly, spaces and all', () => {
    const m = machine([REPO_PARSER], { env: { LITRAG_PARSER: JSON.stringify([exe, '--flag', 'a b']) } });
    expect(resolveCommand(m)).toEqual({ cmd: exe, args: ['--flag', 'a b'] });
  });

  it('says what is wrong with a JSON array that is not one', () => {
    expect(parseCommand('["unclosed', () => false)).toHaveProperty('error');
    expect((parseCommand('[]', () => false) as { error: string }).error).toMatch(/non-empty JSON array of strings/);
    expect(parseCommand('[1, 2]', () => false)).toHaveProperty('error');
  });

  it('still splits the old form on whitespace', () => {
    const m = machine([REPO_PARSER], { env: { LITRAG_PARSER: '  uv run --project /x/parser   litrag-parser ' } });
    expect(resolveCommand(m)).toEqual({ cmd: 'uv', args: ['run', '--project', '/x/parser', 'litrag-parser'] });
  });

  it('takes a value that is an existing file as one program, even with spaces, quoted or not', () => {
    const m = machine([exe], { platform: 'win32', env: { LITRAG_PARSER: exe } });
    expect(resolveCommand(m)).toEqual({ cmd: exe, args: [] });
    const quoted = machine([exe], { platform: 'win32', env: { LITRAG_PARSER: `"${exe}"` } });
    expect(resolveCommand(quoted)).toEqual({ cmd: exe, args: [] });
    const posixPath = '/home/jane/my envs/bin/litrag-parser';
    expect(resolveCommand(machine([posixPath], { env: { LITRAG_PARSER: posixPath } }))).toEqual({ cmd: posixPath, args: [] });
  });

  it('wins over the installed app and the checkout; the option wins over the variable', () => {
    const packaged = machine([], { isPackaged: true, env: { LITRAG_PARSER: '["/elsewhere/litrag-parser"]' } });
    expect(resolveCommand(packaged)).toEqual({ cmd: '/elsewhere/litrag-parser', args: [] });
    const both = machine([REPO_PARSER], { env: { LITRAG_PARSER: 'from-env' }, command: '["from option"]' });
    expect(resolveCommand(both)).toEqual({ cmd: 'from option', args: [] });
    const blank = machine([REPO_PARSER, '/home/jane/.local/bin/uv'], { env: { LITRAG_PARSER: '   ' } });
    expect(resolveCommand(blank)).toEqual({ cmd: '/home/jane/.local/bin/uv', args: devArgs });
  });

  it('shows a command with its spaces quoted', () => {
    expect(displayCommand({ cmd: exe, args: ['--root=/a b', 'x'] })).toBe(`${JSON.stringify(exe)} "--root=/a b" x`);
  });
});

describe('the worker process', () => {
  const scratch = mkdtempSync(join(tmpdir(), 'litrag launch '));
  afterAll(() => rmSync(scratch, { recursive: true, force: true }));

  it('spawns nothing when there is nothing to run, and every request says why', async () => {
    const events: Record<string, unknown>[] = [];
    const w = new ParserWorker({ appDir: scratch, isPackaged: true, env: { LITRAG_VENV: join(scratch, 'no venv') }, onEvent: (e) => events.push(e), onExit: () => undefined });
    expect(w.command).toBeNull();
    w.start();
    expect(events).toEqual([{ event: 'worker-error', message: w.problem }]);
    expect(w.problem).toContain(join(scratch, 'no venv'));
    await expect(w.request('hello')).rejects.toThrow(/Python environment isn't installed/);
  });

  it('runs a program under a path with spaces, its argv whole, with --root and the Python variables', async () => {
    const dir = join(scratch, 'Jane Doe', 'bin');
    mkdirSync(dir, { recursive: true });
    const script = join(dir, 'fake worker.mjs');
    writeFileSync(
      script,
      `import { createInterface } from 'node:readline';
for await (const line of createInterface({ input: process.stdin })) {
  const r = JSON.parse(line);
  if (r.op === 'quit') process.exit(0);
  console.log(JSON.stringify({ event: 'hello', id: r.id, argv: process.argv.slice(2), env: [process.env.PYTHONUNBUFFERED, process.env.PYTHONUTF8, process.env.LITRAG_MARK] }));
}
`,
    );
    const w = new ParserWorker({
      appDir: scratch,
      root: join(scratch, 'my library'),
      command: JSON.stringify([process.execPath, script, 'one arg']),
      env: { ...process.env, LITRAG_MARK: 'kept' },
      onEvent: () => undefined,
      onExit: () => undefined,
    });
    w.start();
    const hello = await w.request('hello');
    expect(hello['argv']).toEqual(['one arg', `--root=${join(scratch, 'my library')}`]);
    expect(hello['env']).toEqual(['1', '1', 'kept']);
    w.stop();
  });

  it('a program that is not there: the reason is kept, and a request waiting on it is answered', async () => {
    const w = new ParserWorker({ appDir: scratch, command: JSON.stringify([join(scratch, 'no such', 'litrag-parser')]), onEvent: () => undefined, onExit: () => undefined });
    w.start();
    const pending = w.request('hello');
    await expect(pending).rejects.toThrow(/would not start/);
    expect(w.problem).toMatch(/would not start .*ENOENT/);
    await expect(w.request('hello')).rejects.toThrow(/ENOENT/);
  });
});
