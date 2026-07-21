import { test, expect } from '../fixtures/test.js'

// Release plan §6 — Star rating. Set a rating in the ImageOverlay, confirm the
// stars fill, then reload and reopen the same picture to prove the score round-
// tripped to the backend and persisted. Setting the same rating again is
// idempotent, so re-runs leave the shared fixture in a stable state. The grid
// test drives the compact star widget on a thumbnail card and asserts the same
// score shows in the overlay — the grid↔overlay sync half of §6. It uses the
// second card so the two tests never write to the same picture.

const RATING = 3
const GRID_RATING = 4

/** Count accent-coloured (filled) stars in any star widget locator. */
function filledStars(starIcons) {
  return starIcons.evaluateAll(
    (els) =>
      els.filter((e) =>
        (e.getAttribute('style') || '').includes('--v-theme-accent'),
      ).length,
  )
}

test.describe('star rating', () => {
  test('sets a rating that persists across a reload (§6)', async ({ page, grid, overlay }) => {
    await grid.goto()
    await overlay.openFromGrid()

    await overlay.setRating(RATING)
    await expect.poll(() => overlay.filledStarCount()).toBe(RATING)

    // The open picture is encoded in the URL (?overlay=<id>), so a reload
    // restores the overlay on the same picture. The rating must survive the
    // round-trip to the backend.
    await page.reload()
    await expect(overlay.root).toBeVisible({ timeout: 15_000 })
    await expect.poll(() => overlay.filledStarCount()).toBe(RATING)
  })

  test('a grid star click saves, survives a reload, and matches the overlay (§6)', async ({
    page,
    grid,
    overlay,
  }) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    const card = grid.thumbnails.nth(1)
    const gridStars = card.locator('.star-overlay--compact .v-icon')

    // The compact star badge is a hover affordance on the thumbnail.
    await card.hover()
    await expect(gridStars.first()).toBeVisible()
    await gridStars.nth(GRID_RATING - 1).click()
    await expect.poll(() => filledStars(gridStars)).toBe(GRID_RATING)

    // Reload: the rating round-tripped to the backend and still shows in the
    // grid badge.
    await page.reload()
    await grid.waitForThumbnailLoaded()
    const cardAfter = grid.thumbnails.nth(1)
    const gridStarsAfter = cardAfter.locator('.star-overlay--compact .v-icon')
    await cardAfter.hover()
    await expect.poll(() => filledStars(gridStarsAfter)).toBe(GRID_RATING)

    // The overlay for the same picture shows the same filled-star count.
    await overlay.openFromGrid(cardAfter)
    await expect.poll(() => overlay.filledStarCount()).toBe(GRID_RATING)
  })
})
