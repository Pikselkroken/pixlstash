import { existsSync, readdirSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../fixtures/test.js'

// Snapshots — Settings → Snapshots. Create a snapshot, change the library,
// restore the snapshot, and the library is back to what it was.
//
// A full restore replaces the whole vault database, and the backend is shared
// by every later spec. So the snapshot restored is the one this test takes at
// its start, and the test asserts the library afterwards matches the library
// before it (sets and their members, people, pictures with their score and
// stack): every later spec reads the vault it would have read without this one.
//
// A full restore also signs out EVERY session and revokes EVERY API token
// (full_restore.py: reset_after_restore, _clear_hub_api_tokens), including the
// session and the ALL token global-setup saved for the whole suite. So the
// test signs in again and mints a new token, rewriting both files the fixtures
// read; afterAll does it if the test never got that far, so a failure here
// cannot cascade into every spec after it.

const __dirname = dirname(fileURLToPath(import.meta.url))
const STATE_PATH = resolve(__dirname, '../.auth/state.json')
const TOKEN_PATH = resolve(__dirname, '../.auth/token.json')

/**
 * Sign in again, save the session for later specs and mint a new ALL token for
 * their apiContext. Returns the signed-in request context; the caller disposes.
 */
async function renewSuiteCredentials(playwright, credentials) {
  const baseURL = test.info().project.use.baseURL
  const ctx = await playwright.request.newContext({ baseURL })
  const login = await ctx.post('/api/v1/login', {
    data: { username: credentials.username, password: credentials.password },
  })
  expect(login.ok(), await login.text()).toBe(true)
  await ctx.storageState({ path: STATE_PATH })
  const minted = await ctx.post('/api/v1/users/me/token', {
    data: { scope: 'ALL', description: 'playwright-e2e' },
  })
  expect(minted.ok(), await minted.text()).toBe(true)
  const { token } = await minted.json()
  writeFileSync(TOKEN_PATH, JSON.stringify({ ...credentials, token }, null, 2))
  return ctx
}

/** The parts of the library a restore must put back, in a comparable form. */
async function libraryFingerprint(api) {
  const read = async (path, params) => {
    const res = await api.get(path, { params })
    expect(res.ok(), `${path}: ${await res.text()}`).toBe(true)
    return res.json()
  }
  const [sets, characters, pictures] = await Promise.all([
    read('/api/v1/picture_sets'),
    read('/api/v1/characters'),
    read('/api/v1/pictures', { fields: 'grid', limit: 1000 }),
  ])
  const membership = await api.post('/api/v1/picture_sets/membership', {
    data: { picture_ids: pictures.map((p) => p.id) },
  })
  expect(membership.ok(), await membership.text()).toBe(true)
  const members = Object.entries(await membership.json())
    .map(([setId, ids]) => `${setId}:${[...ids].sort((a, b) => a - b)}`)
    .sort()
  return {
    sets: sets.map((s) => `${s.id}:${s.name}:${s.picture_count}`).sort(),
    members,
    characters: characters.map((c) => `${c.id}:${c.name}`).sort(),
    pictures: pictures.map((p) => `${p.id}:${p.score}:${p.stack_id}`).sort(),
  }
}

/** Every snapshot manifest under a library's snapshot folder. */
function manifests(libraryPath) {
  const root = join(libraryPath, 'snapshots')
  if (!existsSync(root)) return []
  return readdirSync(root, { recursive: true })
    .filter((name) => String(name).endsWith('.manifest.json'))
    .map((name) => join(root, String(name)))
}

test.describe('snapshots', () => {
  let renewed = false

  test.afterAll(async ({ playwright, credentials }) => {
    if (renewed) return
    await (await renewSuiteCredentials(playwright, credentials)).dispose()
  })

  test('a snapshot taken now restores sets, people and pictures to this state', async ({
    page,
    grid,
    settings,
    apiContext,
    playwright,
    credentials,
  }) => {
    const before = await libraryFingerprint(apiContext)

    // Snapshots are written beside the vault, which here is the throwaway work
    // dir: never a folder outside it.
    const libs = await apiContext.get('/api/v1/libraries')
    expect(libs.ok(), await libs.text()).toBe(true)
    const library = (await libs.json()).libraries.find((l) => l.is_active)
    expect(
      realpathSync(library.path).startsWith(`${realpathSync(tmpdir())}${sep}pixlstash-e2e-`),
      library.path,
    ).toBe(true)
    const manifestsBefore = manifests(library.path)

    await grid.goto()
    await settings.open()
    await settings.openSnapshotsTab()
    await expect.poll(() => settings.snapshotRows.count()).toBeGreaterThan(0)

    const label = `e2e restore point ${Date.now().toString(36)}`
    await settings.snapshotsSection.getByPlaceholder('Label (optional)').fill(label)
    await settings.snapshotsSection.getByRole('button', { name: 'Create now' }).click()
    const row = settings.snapshotRows.filter({ hasText: label })
    await expect(row).toHaveCount(1)
    const added = manifests(library.path).filter((m) => !manifestsBefore.includes(m))
    expect(added, 'one new manifest under the work dir').toHaveLength(1)

    // Change the library after the snapshot: a set of its own, with a member.
    const created = await apiContext.post('/api/v1/picture_sets', {
      data: { name: `e2e-after-snapshot-${Date.now().toString(36)}` },
    })
    expect(created.ok(), await created.text()).toBe(true)
    const { picture_set: set } = await created.json()
    const member = Number(before.pictures[0].split(':')[0])
    const joined = await apiContext.post(`/api/v1/picture_sets/${set.id}/members`, {
      data: { picture_ids: [member] },
    })
    expect(joined.ok(), await joined.text()).toBe(true)
    expect(await libraryFingerprint(apiContext)).not.toEqual(before)

    // Restore everything from the snapshot just taken.
    await row.getByRole('button', { name: /Restore/ }).click()
    const dialog = page.locator('.app-dialog', { hasText: 'Restore from snapshot' })
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('.restore-preview-cp-label')).toHaveText(label)
    const restoreButton = dialog.getByRole('button', { name: 'Restore', exact: true })
    // Nothing happens until the overwrite is acknowledged.
    await expect(restoreButton).toBeDisabled()
    await dialog.getByText('I understand this will').click()
    const restored = page.waitForResponse(
      (res) =>
        /\/api\/v1\/snapshots\/\d+\/restore$/.test(new URL(res.url()).pathname) &&
        res.request().method() === 'POST',
      { timeout: 60_000 },
    )
    await restoreButton.click()
    // Status only: the page reloads the moment the restore answers, which
    // takes the response body with it.
    expect((await restored).status()).toBe(200)

    // The restore signed every session out, this page's included.
    await expect(page.locator('.login-screen')).toBeVisible({ timeout: 30_000 })

    // And revoked the token apiContext holds.
    expect((await apiContext.get('/api/v1/picture_sets')).status()).toBe(401)

    // The set made after the snapshot is gone, and nothing else moved.
    const signedIn = await renewSuiteCredentials(playwright, credentials)
    renewed = true
    try {
      expect(await libraryFingerprint(signedIn)).toEqual(before)
    } finally {
      await signedIn.dispose()
    }
  })
})
