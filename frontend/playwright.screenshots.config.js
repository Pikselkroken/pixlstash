import { defineConfig } from '@playwright/test'

// Screenshot-reproduction harness. Reproduces the marketing-site illustrations
// with the current UI, captured from the real Electron app (see
// e2e/screenshots/electronApp.js) running the demo-data library — varied
// real-world photos, populated people / projects / sets — so the shots look
// like the product users run, not a wall of identical headshots. The global
// setup builds the SPA and the shell and lays out the library; the shell then
// starts the backend itself. The fixture forces a dark theme + sensible grid
// density to match the originals. capture.spec.js takes the stills;
// videos.spec.js records the Getting started videos (needs ffmpeg) and runs
// second, since it changes the library. Everything lands in the gitignored
// website/screenshots/output/; copying into website/assets/ is by hand.
//
// Run:  npm run screenshots          (from frontend/)
//       npm run screenshots 2.0.0    (stamp the captures with a version)
//
// Nothing opens on the desktop: on Linux the app renders through Chromium's
// headless Ozone backend (electronApp.js). Set PIXLSTASH_PYTHON to a venv
// interpreter with pixlstash installed if the checkout has no .venv, and put
// the demo-data library at the repo root (it is not in git).
export default defineConfig({
  testDir: './e2e/screenshots',
  globalSetup: './e2e/screenshots/electron-setup.js',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  use: {
    screenshot: 'off',
    trace: 'off',
  },
})
