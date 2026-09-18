// Build the app once before the suite, so `npx playwright test` needs nothing else.
import { execSync } from 'node:child_process';
import { resolve } from 'node:path';

export default function setup(): void {
  execSync('node build.mjs', { cwd: resolve(import.meta.dirname, '..', '..'), stdio: 'inherit' });
}
