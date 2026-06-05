import { test, expect } from '../fixtures/test.js'

// Release plan §6 — Star rating. Set a rating in the ImageOverlay, confirm the
// stars fill, then reload and reopen the same picture to prove the score round-
// tripped to the backend and persisted. Setting the same rating again is
// idempotent, so re-runs leave the shared fixture in a stable state.

const RATING = 3

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
})
