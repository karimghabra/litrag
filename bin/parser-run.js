#!/usr/bin/env node
// `uv run --project parser …`, keeping the torch the parser's environment
// already has. uv remembers no extra, and a `uv run` without the one the
// environment was synced with syncs it back to PyPI's torch (on Windows the
// CPU-only build). So the build is read off the environment and its extra
// passed on: +cu130 → `--extra cu130`, +cpu → `--extra cpu`, PyPI's → none;
// an environment not yet made gets `--extra cpu` (parser/pyproject.toml).
import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../parser', import.meta.url));
// uv resolves a relative UV_PROJECT_ENVIRONMENT against the project.
const venv = resolve(project, process.env.UV_PROJECT_ENVIRONMENT || '.venv');

/** The installed torch's version ("2.14.0+cu130"), or null when there is none. */
function torchVersion() {
  const sites = [join(venv, 'Lib', 'site-packages')];
  const lib = join(venv, 'lib');
  if (existsSync(lib)) for (const python of readdirSync(lib)) sites.push(join(lib, python, 'site-packages'));
  for (const site of sites) {
    if (!existsSync(site)) continue;
    for (const entry of readdirSync(site)) {
      const m = /^torch-(.+)\.dist-info$/.exec(entry);
      if (m) return m[1];
    }
  }
  return null;
}

const version = torchVersion();
const extra = version === null ? 'cpu' : (/\+(cpu|cu130)$/.exec(version)?.[1] ?? null);
const args = ['run', '--project', project, ...(extra ? ['--extra', extra] : []), ...process.argv.slice(2)];
const result = spawnSync('uv', args, { stdio: 'inherit' });
if (result.error) {
  console.error(`uv: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
