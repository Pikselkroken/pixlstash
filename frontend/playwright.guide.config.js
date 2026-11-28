import { resolve } from 'node:path'

import { defineConfig } from '@playwright/test'

// The interpreter the backend runs under (electron-setup.js reads the same).
const PYTHON = process.env.PIXLSTASH_PYTHON || 'python3'

// Guide illustrations (issue #2825): the capture spec for the in-app Guide,
// kept apart from playwright.screenshots.config.js so it runs without the
// website checkout (websiteDir.js throws without PIXLSTASH_WEBSITE). It shares
// the Electron fixture and global setup with the website captures.
//
// Run:  npm run screenshots:guide     (from frontend/)
// Writes src/assets/guide/<concept-id>.jpg. Commits nothing.
export default defineConfig({
  testDir: './e2e/screenshots',
  testMatch: /guide\.spec\.js$/,
  globalSetup: './e2e/screenshots/electron-setup.js',
  fullyParallel: false,
  workers: 1,
  // A shot can hit a transient backend reset (ECONNRESET on the progress route);
  // one retry clears it, and a shot that still fails stays a failure.
  retries: 1,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  // The Make-more and recipe shots need a ComfyUI to be configured: the same
  // fake server the e2e suite runs (fake_comfyui.py, stdlib only), on the port
  // serve_e2e_backend.py points the backend at.
  webServer: {
    command: `${PYTHON} e2e/fake_comfyui.py`,
    url: 'http://127.0.0.1:9800/system_stats',
    // fake_comfyui.py imports serve_e2e_backend.py, so pixlstash must import.
    env: { PIXLSTASH_E2E_COMFYUI_PORT: '9800', PYTHONPATH: resolve(process.cwd(), '..') },
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
  use: {
    screenshot: 'off',
    trace: 'off',
  },
})
