import { test, expect } from '@playwright/test'

// Reference spec: drives the real app against the seeded test-data/ backend
// through an authenticated owner session (minted in global-setup). Assertions
// are resilient to fixture pruning — they check "at least one" rather than an
// exact count, so changing the number of seed images won't break them.

test.describe('image grid', () => {
  test('renders seeded thumbnails after login', async ({ page }) => {
    await page.goto('/')
    const cards = page.locator('.thumbnail-card')
    await expect(cards.first()).toBeVisible({ timeout: 15_000 })
    expect(await cards.count()).toBeGreaterThan(0)
  })

  test('opens and closes the image overlay', async ({ page }) => {
    await page.goto('/')
    await page.locator('.thumbnail-card').first().click()

    const overlay = page.locator('.image-overlay')
    await expect(overlay).toBeVisible()

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
