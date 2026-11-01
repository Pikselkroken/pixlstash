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
import { mkdirSync, readFileSync } from 'node:fs'
import { delimiter, join } from 'node:path'

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

// A stand-in drive: 2 TB, 40% used. Both numbers, not just the free space: a
// published total is a fact about the capture machine.
const DRIVE_TOTAL = 2_000_000_000_000
const DRIVE_FREE = 1_200_000_000_000

/** Every `{ total_bytes, free_bytes }` in a response, set to the stand-in. */
function easeDrives(value) {
  if (Array.isArray(value)) value.forEach(easeDrives)
  else if (value && typeof value === 'object') {
    if (value.total_bytes) {
      value.total_bytes = DRIVE_TOTAL
      value.free_bytes = DRIVE_FREE
    }
    Object.values(value).forEach(easeDrives)
  }
  return value
}

// A stand-in machine for the Tasks list, which reports the capture machine's
// GPU (its model, its memory, its id) and RAM: 24 GB of VRAM and 64 GB of RAM.
// What is in use is left as measured; only the hardware is replaced.
const STAND_IN_GPU = { uuid: 'GPU-example-0000', name: 'NVIDIA GeForce RTX 4090', total_mb: 24564 }
const STAND_IN_RAM_MB = 65536

/** `GET /workers/progress` with the hardware swapped for the stand-in. */
function standInHardware(body) {
  const resources = body?.resources
  if (resources) {
    for (const device of resources.devices ?? []) {
      // A bigger card can have more in use than the stand-in holds.
      const used = Math.min(device.used_mb ?? 0, STAND_IN_GPU.total_mb)
      Object.assign(device, STAND_IN_GPU)
      device.free_mb = STAND_IN_GPU.total_mb - used
      if (device.budget_mb != null) {
        device.budget_mb = Math.max(0, Math.min(device.budget_mb, device.free_mb))
      }
    }
    for (const holder of resources.holders ?? []) holder.device = STAND_IN_GPU.uuid
    if (resources.ram) {
      const used = Math.min(resources.ram.total_mb - resources.ram.available_mb, STAND_IN_RAM_MB)
      resources.ram.total_mb = STAND_IN_RAM_MB
      resources.ram.available_mb = STAND_IN_RAM_MB - used
    }
  }
  const usage = body?.process
  // The percentage alone gives the real total away (used / percent).
  if (usage?.ram_used_gb != null) {
    usage.ram_total_gb = STAND_IN_RAM_MB / 1024
    usage.ram_used_gb = Math.min(usage.ram_used_gb, usage.ram_total_gb)
    usage.ram_percent = (usage.ram_used_gb / usage.ram_total_gb) * 100
  }
  if (usage?.vram_total_gb) {
    usage.vram_total_gb = STAND_IN_GPU.total_mb / 1024
    if (usage.vram_used_gb != null) {
      usage.vram_used_gb = Math.min(usage.vram_used_gb, usage.vram_total_gb)
      usage.vram_percent = (usage.vram_used_gb / usage.vram_total_gb) * 100
    }
  }
  return body
}

/**
 * Point a page's relative navigations at the backend. Electron windows have no
 * baseURL, and every page object navigates with paths ('/', '/duplicates').
 */
function resolveRelativeGoto(page, base) {
  const goto = page.goto.bind(page)
  page.goto = (url, options) => goto(new URL(url, base).href, options)
}

// The app's one-time notices (useOneTimeNotice), marked seen so none lands in
// a capture: they explain a change to someone upgrading, and every capture
// starts from cleared storage, which reads as a first sight. Set by an init
// script, before the app reads them on each load: written from a test just
// before a navigation, they did not survive it.
const ONE_TIME_NOTICES = ['dedup-migration', 'dedup-sort-migration', 'grid-double-click-to-open']

function markNoticesSeen(notices) {
  for (const name of notices) localStorage.setItem(`pixlstash:seen:${name}`, '1')
}

/**
 * Start a capture from what a fresh browser context used to give it: no
 * per-tab UI state (an inspector left open, a remembered tab) carried over
 * from the one before, since every capture shares one window.
 */
export async function freshUiState(page) {
  await page.evaluate(() => {
    localStorage.clear()
    sessionStorage.clear()
  })
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
          // serve_e2e_backend.py's refusing xdg-open/open stubs, first on the
          // backend's PATH: an export that ends by opening its folder must
          // not open a file manager on the desktop of whoever runs this.
          PATH: [join(info.userData, 'bin'), process.env.PATH].join(delimiter),
        },
        timeout: 120_000,
      })
      await app.context().route(PRODUCTION_HOST_PATTERN, (route) => route.abort())
      // Downloads (an exported ZIP) land in the work dir, not ~/Downloads.
      const downloads = join(info.userData, 'downloads')
      mkdirSync(downloads, { recursive: true })
      await app.evaluate(({ app: shell }, dir) => shell.setPath('downloads', dir), downloads)
      // The drive meters (the title bar's, the model shelf's) read the capture
      // machine's own disk. A marketing shot of a nearly full drive says
      // nothing about the product, so every drive reports the stand-in.
      await app.context().route(DRIVE_ROUTES, async (route) => {
        if (route.request().method() !== 'GET') return route.continue()
        const response = await route.fetch()
        const body = await response.json()
        await route.fulfill({ response, json: easeDrives(body) })
      })
      await app.context().route('**/api/v1/workers/progress', async (route) => {
        const response = await route.fetch()
        await route.fulfill({ response, json: standInHardware(await response.json()) })
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
      await page.addInitScript(markNoticesSeen, ONE_TIME_NOTICES)
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
      // The look of the site's shots, persisted for the owner so every scene
      // inherits it: dark theme, a denser grid, the sidebar expanded (the
      // collapsed rail shows only icons). The first-run prompts are answered
      // so no consent scrim lands in a shot, and the update check and
      // telemetry stay off a throwaway install.
      const config = await ctx.patch('/api/v1/users/me/config', {
        data: {
          check_for_updates: false,
          telemetry_send_install_id: false,
          telemetry_send_feature_usage: false,
          telemetry_send_error_reports: false,
          telemetry_send_hardware_profile: false,
          telemetry_consent_prompted: true,
          theme_mode: 'dark',
          columns: 6,
          sidebar_docked: false,
        },
      })
      if (!config.ok()) throw new Error(`config patch failed: ${config.status()}`)
      // The backend works out demo-data's smart scores as it starts
      // (serve_e2e_backend.py --screenshots). Wait for them, so no capture
      // shows a library half scored. A handful never score: a picture with no
      // aesthetic score has nothing to start from.
      const deadline = Date.now() + 180_000
      for (;;) {
        const res = await ctx.get('/api/v1/pictures', { params: { fields: 'grid', limit: 5000 } })
        const pictures = await res.json()
        const unscored = pictures.filter((p) => p.smart_score == null).length
        if (!pictures.length || unscored / pictures.length < 0.02) break
        if (Date.now() > deadline) throw new Error(`${unscored} pictures still unscored`)
        await new Promise((resolve) => setTimeout(resolve, 2000))
      }
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
