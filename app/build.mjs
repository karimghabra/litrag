// esbuild: main and preload for Electron's Node, the renderer for its Chromium, and pdf.js's worker copied beside them.
import { build } from 'esbuild';
import { cpSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
mkdirSync('dist', { recursive: true });

await build({
  entryPoints: { main: 'src/main/main.ts', preload: 'src/main/preload.ts' },
  outdir: 'dist',
  outExtension: { '.js': '.cjs' },
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  external: ['electron'],
  sourcemap: true,
});

await build({
  entryPoints: { renderer: 'src/renderer/renderer.ts' },
  outdir: 'dist',
  bundle: true,
  platform: 'browser',
  format: 'esm',
  target: 'chrome130',
  sourcemap: true,
});

cpSync('src/renderer/index.html', 'dist/index.html');
cpSync('src/renderer/styles.css', 'dist/styles.css');
const pdfjsDir = dirname(require.resolve('pdfjs-dist/package.json'));
cpSync(join(pdfjsDir, 'build', 'pdf.worker.min.mjs'), 'dist/pdf.worker.min.mjs');
console.log('built dist/');
