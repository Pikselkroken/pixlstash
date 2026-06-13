import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

// Screenshot-reproduction harness. Drives the live SPA to reproduce the
// marketing-site illustrations with the current UI. Unlike the e2e suite it
// boots against the richer **demo-data** library (varied real-world photos,
// populated people / projects / sets) so the shots look like the real product,
// not a wall of identical headshots — on a dedicated port so it never reuses a
// test-data backend a dev already has running. The capture spec then forces a
// dark theme + sensible grid density to match the originals.
//
// Run:  npm run screenshots          (from frontend/)
const PORT = process.env.PIXLSTASH_SHOTS_PORT || '9610'
const BASE_URL = `http://127.0.0.1:${PORT}`
const DEMO_DATA = resolve(process.cwd(), '..', 'demo-data')

export default defineConfig({
  testDir: './e2e/screenshots',
  globalSetup: './e2e/global-setup.js',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  use: {
    baseURL: BASE_URL,
    storageState: './e2e/.auth/state.json',
    // Roomy, high-DPI viewport so the reproduced shots are crisp and framed
    // close to the originals on the site.
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 2,
    screenshot: 'off',
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npm run build && ${process.env.PIXLSTASH_PYTHON || 'python'} e2e/serve_e2e_backend.py`,
    url: `${BASE_URL}/version`,
    timeout: 240_000,
    // Always boot a fresh demo-data backend (correct library + first-run state).
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
    env: {
      PIXLSTASH_E2E_PORT: PORT,
      PIXLSTASH_E2E_DATA: DEMO_DATA,
    },
  },
})
