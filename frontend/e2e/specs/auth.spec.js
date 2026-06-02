import { test, expect, loginToFreshSession } from '../fixtures/test.js'
import { SettingsDialog } from '../pages/SettingsDialog.js'

// Release plan §1 — Authentication. The backend tracks sessions in-memory and
// logout() pops the session id, so the logout test runs in an *isolated*
// freshly-minted session (loginToFreshSession) to avoid invalidating the
// shared storageState session used by every other spec.

test.describe('authentication', () => {
  test('keeps the session across a reload (§1.3)', async ({ page, grid }) => {
    await grid.goto()
    await page.reload()
    await expect(page.locator('.login-screen')).toHaveCount(0)
    await expect(grid.thumbnails.first()).toBeVisible({ timeout: 15_000 })
  })

  test('redirects an unauthenticated visitor to login (§1.1)', async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, storageState: undefined })
    const page = await context.newPage()
    await page.goto('/')
    await expect(page.locator('.login-screen')).toBeVisible()
    await expect(page.locator('.thumbnail-card')).toHaveCount(0)
    await context.close()
  })

  test('generates an API token from settings (§1.2)', async ({ grid, settings }) => {
    await grid.goto()
    await settings.open()
    await settings.openAccountTab()
    const before = await settings.tokenRows.count()

    const description = 'e2e release token'
    await settings.tokenDescription.fill(description)
    await settings.createTokenButton.click()

    // The new token appears in the list (a reveal dialog may overlay it, but
    // toBeVisible does not check occlusion). Escape is avoided — it closes the
    // whole settings dialog.
    await expect(
      settings.tokenRows.filter({ hasText: description }).first(),
    ).toBeVisible()
    expect(await settings.tokenRows.count()).toBe(before + 1)
  })

  test('logs out via settings (§1.1)', async ({ browser, baseURL }) => {
    const { context, page } = await loginToFreshSession(browser, baseURL)
    try {
      await page.goto('/')
      await expect(page.locator('.thumbnail-card').first()).toBeVisible({
        timeout: 15_000,
      })

      const settings = new SettingsDialog(page)
      await settings.open()
      await settings.logoutButton.click()

      await expect(page.locator('.login-screen')).toBeVisible()
    } finally {
      await context.close()
    }
  })
})
