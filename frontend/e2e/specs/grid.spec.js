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

  test('opens and closes the image overlay', async ({ page }) => {
    await page.goto('/')

    const overlay = page.locator('.image-overlay')
    // The grid re-renders on picture websocket events (the e2e backend's
    // thumbnail-upgrade job emits a steady stream of them), which can swap the
    // card out between the actionability check and the click, swallowing it.
    // Retry until the overlay is actually up. This spec deliberately avoids
    // page objects (the retry is inline rather than borrowing ImageOverlay),
    // which is unrelated to importing `test` from the shared fixtures file
    // above — that import is only for the production-network block.
    await expect(async () => {
      await page.locator('.thumbnail-card').first().click()
      await expect(overlay).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 30_000 })

    // Close via the dedicated button (Escape first reveals chrome).
    await overlay.locator('.overlay-close').click()
    await expect(overlay).toBeHidden()
  })

  test('opens the search menu with the F shortcut', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.thumbnail-card').first()).toBeVisible({
      timeout: 15_000,
    })

    // Search is an icon-trigger popover in the grid toolbar (it replaced the old
    // full-screen search popup). The "F" shortcut opens it and focuses the field.
    await page.keyboard.press('f')

    const panel = page.locator('.gb-search-panel')
    await expect(panel).toBeVisible()
    await expect(panel.locator('input')).toBeFocused()

    await page.keyboard.press('Escape')
    await expect(panel).toBeHidden()
  })
})
