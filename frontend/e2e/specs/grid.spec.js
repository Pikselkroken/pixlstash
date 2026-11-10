// The shared fixture (not raw @playwright/test) so this context gets the
// production-network block registered on the browser fixture (issue #1213).
import { test, expect } from '../fixtures/test.js'

// Reference spec: drives the real app against the seeded test-data/ backend
// through an authenticated owner session (minted in global-setup). Assertions
// are resilient to fixture pruning — they check "at least one" rather than an
// exact count, so changing the number of seed images won't break them.

test.describe('image grid', () => {
  test('renders seeded thumbnails after login', async ({ page }) => {
    await page.goto('/')
    const cards = page.locator('.thumbnail-card')
    await expect(cards.first()).toBeVisible({ timeout: 15_000 })
    await expect.poll(() => cards.count()).toBeGreaterThan(0)
  })

  test('opens and closes the image lightbox', async ({ page }) => {
    await page.goto('/')

    const lightbox = page.locator('.lightbox')
    // The grid re-renders on picture websocket events (the e2e backend's
    // thumbnail-upgrade job emits a steady stream of them), which can swap the
    // card out between the actionability check and the click, swallowing it.
    // Retry until the lightbox is actually up. This spec deliberately avoids
    // page objects (the retry is inline rather than borrowing Lightbox),
    // which is unrelated to importing `test` from the shared fixtures file
    // above — that import is only for the production-network block.
    await expect(async () => {
      await page.locator('.thumbnail-card').first().dblclick()
      await expect(lightbox).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 30_000 })

    // Close via the toolbar's way back to the grid.
    await lightbox.locator('.lightbox-back').click()
    await expect(lightbox).toBeHidden()
  })

  test('focuses the search field with the F shortcut', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.thumbnail-card').first()).toBeVisible({
      timeout: 15_000,
    })

    // Search is the toolbar field itself. The "F" shortcut widens it, drops
    // its list and puts the caret in it.
    await page.keyboard.press('f')

    const panel = page.locator('.stf-drop')
    await expect(panel).toBeVisible()
    await expect(page.locator('input.stf-input')).toBeFocused()

    await page.keyboard.press('Escape')
    await expect(panel).toBeHidden()
  })
})
