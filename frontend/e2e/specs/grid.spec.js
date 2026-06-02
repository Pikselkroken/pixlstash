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

  test('opens the search overlay from the toolbar', async ({ page }) => {
    await page.goto('/')
    // The magnify button toggles searchStore.searchOverlayVisible.
    await page.locator('.mdi-magnify').first().click()

    const search = page.locator('.search-overlay')
    await expect(search).toBeVisible()
    await expect(search.locator('input').first()).toBeFocused()

    await page.keyboard.press('Escape')
    await expect(search).toBeHidden()
  })
})
