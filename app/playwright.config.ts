// The end-to-end suite: the real window, the real worker, real papers.
//   npx playwright test                 the JATS fixture (no models, no GPU)
//   LITRAG_E2E_PAPERS=<dir> npx playwright test    every PDF and XML in a folder, on the GPU
//   LITRAG_HEADLESS=1 npx playwright test          the window hidden, rendered offscreen: no screen needed
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 20 * 60 * 1000, // a folder of PDFs takes minutes on the first run
  expect: { timeout: 30 * 1000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'e2e-report' }]],
  outputDir: 'e2e-out',
  globalSetup: './tests/e2e/setup.ts',
  use: { trace: 'retain-on-failure', screenshot: process.env['LITRAG_HEADLESS'] === '1' ? 'off' : 'only-on-failure' },
});
