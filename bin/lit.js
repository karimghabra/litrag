#!/usr/bin/env node
// `lit` runs from TypeScript source; this shim adds the flag Node needs for that.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const cli = fileURLToPath(new URL('../src/cli.ts', import.meta.url));
const result = spawnSync(process.execPath, ['--experimental-transform-types', '--no-warnings', cli, ...process.argv.slice(2)], { stdio: 'inherit' });
process.exit(result.status ?? 1);
