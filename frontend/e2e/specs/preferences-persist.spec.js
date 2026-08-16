import { test, expect } from '../fixtures/test.js'

// Preferences take a long road: a control writes a store, a watcher notices and
// PATCHes /users/me/config, and the next session reads it back. The Phase 3
// refactor moved every step of that - the guarded setters onto the stores, the
// fourteen persistence watchers into useAppConfig - and nothing exercised the
// round trip, so a preference that silently stopped persisting would have gone
// unnoticed until a user complained their settings kept resetting.
//
// Compact mode is the cheapest control to drive: a real button in the toolbar's
// Grid-view menu, writing straight to the grid store.

/**
 * Force compact mode OFF through the API, so a click can only ever turn it on.
 *
 * Load-bearing, not tidiness. `useGridStore` initialises `compactMode` to false
 * for an owner session, so a test that starts from compact ON would assert the
 * reload produced OFF - which the store's pre-fetch default already satisfies,
 * before the config GET has even landed. That assertion passes without the
 * server having stored anything. Nothing guarantees the starting state: the
 * suite shares one mutable backend, and CI runs with `retries: 1`, so a failed
 * attempt would otherwise hand the next one exactly that vacuous pass.
 */
async function compactModeOff(apiContext) {
  const res = await apiContext.patch('/api/v1/users/me/config', {
    data: { compact_mode: false },
  })
  expect(res.ok()).toBe(true)
}

/**
 * Resolve once a config PATCH carrying `key` has been ANSWERED by the backend.
 *
 * The watcher fires the PATCH the moment the store changes, so an assertion on
 * the request alone is satisfied while it is still in flight; reloading on the
 * next line cancels it and the server never stores the change (#973). Arm this
 * BEFORE the click, await it before reloading.
 *
 * The status is deliberately NOT part of the predicate: a non-2xx PATCH should
 * fail on `expect(res.ok())` naming the status, not time out as a non-match.
 */
function configPatched(page, key) {
  return page.waitForResponse(
    (res) => {
      if (
        !res.url().includes('/users/me/config') ||
        res.request().method() !== 'PATCH'
      ) {
        return false
      }
      const body = res.request().postDataJSON()
      return Boolean(body) && key in body
    },
    // Playwright's default action timeout is 0 (= no timeout), which would turn
    // a missing PATCH into a bare 30 s test timeout instead of naming the wait.
    { timeout: 10_000 },
  )
}

/** Open the toolbar's "Grid view" menu and return its Compact toggle. */
async function openViewMenu(page) {
  await page.locator('.bar-btn:has(.mdi-view-grid)').first().click()
  const compact = page.locator('.tbm-btn--compact')
  await expect(compact).toBeVisible()
  return compact
}

test.describe('preferences round-trip', () => {
  test('a compact-mode change is PATCHed and survives a reload', async ({
    page,
    apiContext,
  }) => {
    await compactModeOff(apiContext)

    await page.goto('/')
    await expect(page.locator('.thumbnail-card').first()).toBeVisible({
      timeout: 15_000,
    })

    const compact = await openViewMenu(page)
    const patched = configPatched(page, 'compact_mode')
    await compact.click()

    // The change reached the server AND the server accepted it, not just the store.
    const res = await patched
    expect(res.ok()).toBe(true)
    expect(res.request().postDataJSON().compact_mode).toBe(true)

    // And it is what the next session gets. Compact ON cannot come from the
    // store's default, so this only passes if the backend stored the change.
    await page.reload()
    await expect(page.locator('.thumbnail-card').first()).toBeVisible({
      timeout: 15_000,
    })
    const afterReload = await openViewMenu(page)
    await expect
      .poll(() => afterReload.evaluate((el) => el.classList.contains('tbm-btn--on')), {
        timeout: 10_000,
      })
      .toBe(true)

    // Undo this test's own write, so the shared backend is left with compact
    // mode off - the state this test forced on the way in, not whatever it
    // found. Through the API rather than a second click: the watcher
    // early-returns while a config fetch is still in flight, so a UI restore
    // can legitimately send nothing at all to wait for.
    await compactModeOff(apiContext)
  })
})
