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
