import { test as base, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { GridPage } from '../pages/GridPage.js'
import { ImageOverlay } from '../pages/ImageOverlay.js'
import { SettingsDialog } from '../pages/SettingsDialog.js'
import { SideBar } from '../pages/SideBar.js'
import { ShareDialog } from '../pages/ShareDialog.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const TOKEN_PATH = resolve(__dirname, '../.auth/token.json')

// Per-run credentials minted by global-setup (token + username + password).
// The ALL-scope token authenticates the apiContext; the password lets the
// logout spec mint an isolated session (see loginToFreshSession).
function readCredentials() {
  return JSON.parse(readFileSync(TOKEN_PATH, 'utf8'))
}

/**
 * Read the SPA's per-tab client id from sessionStorage. This is the id
 * `apiClient` attaches as `X-Client-Id` on every mutating request, and the id
 * `useGridRealtimeSync` compares against an event's `origin_client_id` to decide
 * own-echo (suppress) vs. foreign (pill). The grid-refresh own-change tests
 * drive a mutation through `apiContext` WITH this header to simulate the user's
 * own action from their own tab. Must be read AFTER the SPA has booted (the id
 * is generated lazily by useWsStore on first store access).
 *
 * @param {import('@playwright/test').Page} page
 * @returns {Promise<string|null>}
 */
export function readClientId(page) {
  return page.evaluate(() => sessionStorage.getItem('pixlstash:clientId'))
}

/**
 * Attach a WebSocket frame sniffer to a page and return a live handle for
 * asserting what the grid actually received. The grid is fully event-driven
 * over /ws/updates, so counting the inbound frames is the direct measurement
 * for both grid-refresh defect classes: "how many refresh-driving events
 * arrived" (continuous-refresh) and "did an event carry origin_client_id"
 * (pill-on-own-change).
 *
 * Attaches `page.on('websocket')` + `ws.on('framereceived')` and parses every
 * inbound text frame as JSON (non-JSON / control frames are ignored). Capture
 * starts the moment this is called, so call it in the test body (or beforeEach)
 * BEFORE the page navigates — the SPA opens /ws/updates during load, and a
 * listener attached during fixture setup (before the per-test page lifecycle)
 * misses that socket on the 2nd+ test of a file. Attaching right before
 * `grid.goto()` is reliable across the whole file.
 *
 * @param {import('@playwright/test').Page} page
 * @returns {{
 *   frames: Array<Object>,
 *   ofType: (type: string) => Array<Object>,
 *   countOfType: (type: string) => number,
 *   gridDriving: () => Array<Object>,
 *   clear: () => void,
 * }}
 */
export function attachWsSniffer(page) {
  // Grid-driving wire types: the message types App.vue routes into the grid
  // realtime-sync decision table or the wsTagUpdate path. These are the frames
  // that can move the grid / raise a pill.
  const GRID_DRIVING_TYPES = new Set([
    'pictures_changed',
    'picture_imported',
    'tags_changed',
    'descriptions_changed',
    'characters_changed',
  ])
  const frames = []

  page.on('websocket', (ws) => {
    // Only the updates socket carries grid events; ignore any other socket.
    if (!ws.url().includes('/ws/updates')) return
    ws.on('framereceived', (data) => {
      const text = typeof data.payload === 'string' ? data.payload : null
      if (text == null) return
      let parsed
      try {
        parsed = JSON.parse(text)
      } catch {
        // Non-JSON frame (heartbeat / control); not grid-relevant.
        return
      }
      frames.push(parsed)
    })
  })

  return {
    frames,
    ofType: (type) => frames.filter((f) => f?.type === type),
    countOfType: (type) => frames.filter((f) => f?.type === type).length,
    gridDriving: () => frames.filter((f) => GRID_DRIVING_TYPES.has(f?.type)),
    clear: () => {
      frames.length = 0
    },
  }
}

/**
 * Mint a brand-new backend session and return a page that uses ONLY that
 * session — never the shared storageState cookie. Use this for any spec that
 * invalidates its own session (logout), so it cannot pop the shared session
 * out of the backend's in-memory active_session_ids map and break other specs.
 */
export async function loginToFreshSession(browser, baseURL) {
  const { username, password } = readCredentials()
  // A storage-less context: POST /login mints a fresh session_id cookie that
  // belongs to this context alone.
  const context = await browser.newContext({ baseURL, storageState: undefined })
  const res = await context.request.post('/api/v1/login', {
    data: { username, password },
  })
  if (!res.ok()) {
    await context.close()
    throw new Error(`fresh login failed (${res.status()}): ${await res.text()}`)
  }
  const page = await context.newPage()
  return { context, page }
}

export const test = base.extend({
  // The minted credentials object: { token, username, password }.
  credentials: async ({}, use) => {
    await use(readCredentials())
  },

  // An authenticated API request context (ALL scope) for reading entity IDs
  // and asserting backend truth after a UI mutation. Honours the project
  // baseURL so it hits the same e2e backend the browser does.
  apiContext: async ({ playwright, baseURL }, use) => {
    const { token } = readCredentials()
    const ctx = await playwright.request.newContext({
      baseURL,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    })
    await use(ctx)
    await ctx.dispose()
  },

  // Page-object helpers bound to the current page.
  grid: async ({ page }, use) => {
    await use(new GridPage(page))
  },
  overlay: async ({ page }, use) => {
    await use(new ImageOverlay(page))
  },
  settings: async ({ page }, use) => {
    await use(new SettingsDialog(page))
  },
  sidebar: async ({ page }, use) => {
    await use(new SideBar(page))
  },
  shareDialog: async ({ page }, use) => {
    await use(new ShareDialog(page))
  },
})

export { expect }
