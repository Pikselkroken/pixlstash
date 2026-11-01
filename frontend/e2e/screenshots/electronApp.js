// Drive the real desktop app for the website captures. The Electron shell is
// launched in its dev-backend mode with its userData pointed at the e2e work
// dir that `serve_e2e_backend.py --screenshots` built from demo-data, so it
// finds a server-config there, skips first-run setup and starts the backend on
// that throwaway library itself, with its own port and desktop session. What
// the captures show is therefore the shipped preload, title bar and window,
// not a stub of them.
//
// One app per worker (workers: 1), one window: each scene navigates it afresh.
import { _electron as electron, request, test as base } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { WORK_INFO } from './electron-setup.js'

// Same frame as the old browser captures: 1600x1000 CSS px at 2x.
const WIDTH = 1600
const HEIGHT = 1000

// Never reach production from a capture run: the update check and telemetry
// would otherwise report a fake install against the real site.
const PRODUCTION_HOST_PATTERN = 'https://pixlstash.dev/**'

// Set by `npm run screenshots <version>` (run.js). The SPA build already
// carries it (vite's __APP_VERSION__, electron-setup.js); this covers App.vue's
// runtime GET /version, so no capture can show two different versions.
const VERSION_OVERRIDE = process.env.PIXLSTASH_VERSION_OVERRIDE || ''

// The routes that report a drive's size and free space.
const DRIVE_ROUTES = /\/api\/v1\/(libraries|model-folders\/devices)(\?|$)/

/** Every `{ total_bytes, free_bytes }` in a response, set to 60% free. */
function easeDrives(value) {
  if (Array.isArray(value)) value.forEach(easeDrives)
  else if (value && typeof value === 'object') {
    if (value.total_bytes) value.free_bytes = Math.round(value.total_bytes * 0.6)
    Object.values(value).forEach(easeDrives)
  }
  return value
}

/**
 * Point a page's relative navigations at the backend. Electron windows have no
 * baseURL, and every page object navigates with paths ('/', '/duplicates').
 */
function resolveRelativeGoto(page, base) {
  const goto = page.goto.bind(page)
  page.goto = (url, options) => goto(new URL(url, base).href, options)
}

export const test = base.extend({
  electronApp: [
    // Playwright reads a fixture's dependencies from this pattern; none here.
    // eslint-disable-next-line no-empty-pattern
    async ({}, use) => {
      const info = JSON.parse(readFileSync(WORK_INFO, 'utf8'))
      const app = await electron.launch({
        executablePath: info.electron,
        args: [
          join(info.electronDir, 'dist', 'main.js'),
          '--force-device-scale-factor=2',
          // Render off screen: no window on the desktop for the length of the
          // run. Chromium's headless Ozone backend, which Electron carries on
          // Linux; elsewhere the flag is ignored and the window shows.
          '--ozone-platform=headless',
        ],
        cwd: info.electronDir,
        env: {
          ...process.env,
          PIXLSTASH_DESKTOP_DEV: '1',
          PIXLSTASH_DEV_BACKEND: info.python,
          PIXLSTASH_DESKTOP_USER_DATA: info.userData,
        },
        timeout: 120_000,
      })
      await app.context().route(PRODUCTION_HOST_PATTERN, (route) => route.abort())
      // The drive meters (the title bar's, the model shelf's) read the capture
      // machine's own disk. A marketing shot of a nearly full drive says
      // nothing about the product, so every drive reports 40% used.
      await app.context().route(DRIVE_ROUTES, async (route) => {
        if (route.request().method() !== 'GET') return route.continue()
        const response = await route.fetch()
        const body = await response.json()
        await route.fulfill({ response, json: easeDrives(body) })
      })
      if (VERSION_OVERRIDE) {
        await app.context().route('**/version', async (route) => {
          const response = await route.fetch()
          const body = await response.json()
          await route.fulfill({ json: { ...body, version: VERSION_OVERRIDE } })
        })
      }
      await use(app)
      await app.close()
    },
    { scope: 'worker', timeout: 240_000 },
  ],

  // The window, once it has left the splash for the backend.
  appWindow: [
    async ({ electronApp }, use) => {
      const page = await electronApp.firstWindow()
      await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 180_000 })
      await electronApp.evaluate(({ BrowserWindow }, [w, h]) => {
        const win = BrowserWindow.getAllWindows()[0]
        win.unmaximize()
        win.setContentSize(w, h)
      }, [WIDTH, HEIGHT])
      resolveRelativeGoto(page, new URL(page.url()).origin)
      await use(page)
    },
    { scope: 'worker' },
  ],

  // An owner API client on the same backend: the window's desktop session
  // cookie mints a full-access token, as global-setup does for the e2e suite.
  apiContext: [
    async ({ electronApp, appWindow }, use) => {
      const origin = new URL(appWindow.url()).origin
      const cookies = await electronApp.context().cookies(origin)
      const session = cookies.find((c) => c.name === 'session_id')
      if (!session) throw new Error('the desktop window has no session_id cookie')
      const cookieCtx = await request.newContext({
        baseURL: origin,
        extraHTTPHeaders: { Cookie: `session_id=${session.value}` },
      })
      const minted = await cookieCtx.post('/api/v1/users/me/token', {
        // Named like a real one: it is listed in the Account & tokens shots.
        data: { scope: 'ALL', description: 'ComfyUI' },
      })
      if (!minted.ok()) {
        throw new Error(`token mint failed (${minted.status()}): ${await minted.text()}`)
      }
      const { token } = await minted.json()
      await cookieCtx.dispose()
      const ctx = await request.newContext({
        baseURL: origin,
        extraHTTPHeaders: { Authorization: `Bearer ${token}` },
      })
      await use(ctx)
      await ctx.dispose()
    },
    { scope: 'worker' },
  ],

  page: async ({ appWindow }, use) => {
    await use(appWindow)
  },
})

export { expect } from '@playwright/test'
