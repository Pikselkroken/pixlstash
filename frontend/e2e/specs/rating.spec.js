import { test, expect } from '../fixtures/test.js'

// Release plan §6 — Star rating. Set a rating in the ImageOverlay, confirm the
// stars fill, then reload and reopen the same picture to prove the score round-
// tripped to the backend and persisted. The grid test drives the compact star
// widget on a thumbnail card and asserts the same score shows in the overlay —
// the grid↔overlay sync half of §6. It uses the second card so the two tests
// never write to the same picture.
//
// Both tests force the target picture's score to 0 through the API before
// touching the widget. That is load-bearing, not tidiness: see `resetScore`.

const RATING = 3
const GRID_RATING = 4

/**
 * Force a picture's score to 0 so a click can only ever SET the rating.
 *
 * The star widget toggles (`toggleScore`: clicking the rating a picture already
 * has clears it to 0). So a test that clicks star N is only meaningful if the
 * picture is not already on N — otherwise the click clears the score and the
 * test reads back 0. Nothing guarantees that starting state: the suite shares
 * one mutable backend, and on `retries` a failed attempt leaves its own write
 * behind for the next one to toggle off. Resetting first makes each attempt
 * independent of whatever ran before it, including itself.
 */
async function resetScore(apiContext, pictureId) {
  const res = await apiContext.post('/api/v1/pictures/apply-scores', {
    data: { scores: { [String(pictureId)]: 0 }, only_unscored: false },
  })
  expect(res.ok()).toBe(true)
}

/**
 * Resolve once a score write has actually reached the backend.
 *
 * Both star widgets update OPTIMISTICALLY: the click sets the score locally and
 * fires POST /pictures/apply-scores separately. A filled-star assertion proves
 * only that the UI moved, so reloading while that POST is still in flight aborts
 * it and the rating is lost. Arm this BEFORE the click, await it before reloading.
 */
function scoreWritten(page) {
  return page.waitForResponse(
    (res) =>
      res.url().includes('/pictures/apply-scores') &&
      res.request().method() === 'POST' &&
      res.ok(),
  )
}

/** The picture the overlay currently has open, from `?overlay=<id>`. */
function openPictureId(page) {
  return new URL(page.url()).searchParams.get('overlay')
}

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
  test('sets a rating that persists across a reload (§6)', async ({
    page,
    grid,
    overlay,
    apiContext,
  }) => {
    await grid.goto()
    await overlay.openFromGrid()

    await resetScore(apiContext, openPictureId(page))
    await page.reload()
    await expect(overlay.root).toBeVisible({ timeout: 15_000 })
    await expect.poll(() => overlay.filledStarCount()).toBe(0)

    const written = scoreWritten(page)
    await overlay.setRating(RATING)
    await expect.poll(() => overlay.filledStarCount()).toBe(RATING)
    // Only the UI is proven so far; wait for the write before reloading.
    await written

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
    apiContext,
  }) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    // The card carries no picture id, so learn it from the overlay's URL, then
    // reset that picture's score before driving the grid widget.
    await overlay.openFromGrid(grid.thumbnails.nth(1))
    await resetScore(apiContext, openPictureId(page))

    await page.goto('/')
    await grid.waitForThumbnailLoaded()

    const card = grid.thumbnails.nth(1)
    const gridStars = card.locator('.star-overlay--compact .v-icon')

    // The compact star badge is a hover affordance on the thumbnail.
    await card.hover()
    await expect(gridStars.first()).toBeVisible()
    const written = scoreWritten(page)
    await gridStars.nth(GRID_RATING - 1).click()
    await expect.poll(() => filledStars(gridStars)).toBe(GRID_RATING)
    await written

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
