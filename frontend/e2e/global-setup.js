import { request } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const AUTH_DIR = resolve(__dirname, '.auth')
const STATE_PATH = resolve(AUTH_DIR, 'state.json')
const TOKEN_PATH = resolve(AUTH_DIR, 'token.json')

// Credentials are generated fresh every run and never committed. The backend
// boots in first-run state (the launcher strips user/token rows from its
// throwaway copy of the fixture), so the first POST /login registers this
// admin and returns an owner session cookie.
const USERNAME = 'e2e-admin'
const PASSWORD = randomBytes(18).toString('base64url')

async function waitForServer(ctx, baseURL) {
  const deadline = Date.now() + 120_000
  let lastErr
  while (Date.now() < deadline) {
    try {
      const res = await ctx.get(`${baseURL}/version`)
      if (res.ok()) return
    } catch (err) {
      lastErr = err
    }
    await new Promise((r) => setTimeout(r, 1000))
  }
  throw new Error(`e2e backend did not become ready at ${baseURL}: ${lastErr}`)
}

export default async function globalSetup(config) {
  const baseURL =
    config.projects[0]?.use?.baseURL || 'http://127.0.0.1:9600'
  const ctx = await request.newContext({ baseURL })

  await waitForServer(ctx, baseURL)

  // Register + log in (first-run): sets the owner session cookie on ctx.
  const login = await ctx.post('/api/v1/login', {
    data: { username: USERNAME, password: PASSWORD },
  })
  if (!login.ok()) {
    throw new Error(
      `e2e registration failed (${login.status()}): ${await login.text()}. ` +
        `The fixture vault must boot in first-run state — check serve_e2e_backend.py stripped the user table.`,
    )
  }

  // Mint a full-access (ALL) bearer token for any test that needs to drive
  // <img>/share requests via an Authorization header instead of the cookie.
  const tokenRes = await ctx.post('/api/v1/users/me/token', {
    data: { scope: 'ALL', description: 'playwright-e2e' },
  })
  if (!tokenRes.ok()) {
    throw new Error(
      `e2e token mint failed (${tokenRes.status()}): ${await tokenRes.text()}`,
    )
  }
  const { token } = await tokenRes.json()

  // Decide the first-run "Check for updates automatically?" prompt up front so
  // its modal scrim never blocks test interactions (null = undecided = shown).
  const cfg = await ctx.patch('/api/v1/users/me/config', {
    data: { check_for_updates: false },
  })
  if (!cfg.ok()) {
    throw new Error(
      `e2e config patch failed (${cfg.status()}): ${await cfg.text()}`,
    )
  }

  mkdirSync(AUTH_DIR, { recursive: true })
  await ctx.storageState({ path: STATE_PATH })
  // Persist the per-run credentials so specs can mint an *isolated* session
  // when needed (e.g. the logout test, which must not pop the shared session
  // out of the backend's in-memory active_session_ids map). All values live
  // only in the gitignored .auth/ dir for the duration of the run.
  writeFileSync(
    TOKEN_PATH,
    JSON.stringify({ token, username: USERNAME, password: PASSWORD }, null, 2),
  )
  await ctx.dispose()
}
