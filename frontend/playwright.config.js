import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// The e2e backend (frontend/e2e/serve_e2e_backend.py) serves both the built
// SPA and the API on this single origin, so cookie auth works with no CORS.
//
// The port is derived from the checkout path rather than fixed, because two
// working copies of the repo running e2e at the same time would otherwise both
// try to bind 9600. The failure that causes is not an obvious one: whichever
// run loses the race can end up talking to the OTHER checkout's server, whose
// vault has a different admin password, and global-setup reports the fixture
// is not in first-run state. Set PIXLSTASH_E2E_PORT to pin it.
const PORT =
  process.env.PIXLSTASH_E2E_PORT ||
  String(
    9600 +
      ([...fileURLToPath(new URL('.', import.meta.url))].reduce(
        (h, ch) => (h * 31 + ch.charCodeAt(0)) % 200,
        7,
      ) %
        200),
  )
const BASE_URL = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './e2e/specs',
  globalSetup: './e2e/global-setup.js',
  // One worker against one shared, mutable backend — tests must not race.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    // Authenticated owner session minted once in global-setup.
    storageState: './e2e/.auth/state.json',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    // Build the SPA (served by the Python server), then launch the backend
    // against a throwaway copy of test-data/. Override the interpreter with
    // PIXLSTASH_PYTHON if `python` is not the pixlstash venv on your PATH.
    command: `npm run build && PIXLSTASH_E2E_PORT=${PORT} ${process.env.PIXLSTASH_PYTHON || 'python'} e2e/serve_e2e_backend.py`,
    url: `${BASE_URL}/version`,
    timeout: 180_000,
    // Always boot a fresh backend: the launcher strips users so each run
    // registers a brand-new admin with a per-run random password (no reusable
    // credential). Reusing a server would leave that admin's password stale.
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
    // Import pixlstash from THIS checkout. A shared venv with an editable
    // install elsewhere otherwise wins (its finder resolves what cwd cannot),
    // and the run silently serves another worktree's dist.
    env: { PYTHONPATH: resolve(process.cwd(), '..') },
  },
})
