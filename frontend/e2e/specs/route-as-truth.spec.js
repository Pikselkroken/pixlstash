import { test, expect } from '../fixtures/test.js'

// Phase 0 characterization pin — ROUTE AS THE SINGLE SOURCE OF TRUTH.
// (frontend_architecture.md §2 Key Design Principles; frontend_refactoring plan
// Phase 0; issue #459 alignment rule 3.)
//
// The invariant: the URL route is the single source of truth for what the grid
// shows. Only explicit entry clicks push routes; the grid follows the route in
// every direction — entry click, browser Back/Forward, and a cold load straight
// into an entity URL. Phase 3 moves App.vue's route watcher into a store while
// keeping the route as the only writer; this pins the behaviour that migration
// must preserve. Runnable now, but needs the live e2e app (Playwright + backend).

test.describe('the route drives the grid', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('entry click pushes a route; Back/Forward move the grid to match', async ({
    page,
    grid,
    sidebar,
  }) => {
    await grid.waitForThumbnailLoaded()
    expect(page.url()).not.toContain('/set/')
    const allViewFirst = await grid.firstThumbnailKey()

    // Entry click — the one action that navigates.
    const row = await sidebar.firstNonEmpty(sidebar.setItems)
    await row.click()
    await expect.poll(() => page.url()).toContain('/set/')
    await grid.waitForThumbnailLoaded()
    const setUrl = page.url()

    // Back → route returns to the all-view → the grid follows the route.
    await page.goBack()
    await expect.poll(() => page.url()).not.toContain('/set/')
    await grid.waitForThumbnailLoaded()
    await expect
      .poll(() => grid.firstThumbnailKey(), {
        message: 'grid must return to the all-view leader when the route does',
      })
      .toBe(allViewFirst)

    // Forward → route returns to the set → the grid follows again.
    await page.goForward()
    await expect.poll(() => page.url()).toBe(setUrl)
    await grid.waitForThumbnailLoaded()
    await expect(row).toHaveClass(/active/)
  })

  test('a cold load into an entity URL resolves the grid from the route alone', async ({
    page,
    grid,
    sidebar,
  }) => {
    // Discover a real set URL via an entry click first.
    const row = await sidebar.firstNonEmpty(sidebar.setItems)
    await row.click()
    await expect.poll(() => page.url()).toContain('/set/')
    const setUrl = page.url()

    // Navigate cold into that URL: no prior in-app state, only the route.
    await page.goto(setUrl)
    await grid.waitForThumbnailLoaded()
    expect(page.url()).toBe(setUrl)
    // The sidebar reflects the route: the set row is active on load.
    const activeRow = await sidebar.firstNonEmpty(sidebar.setItems)
    await expect(activeRow).toHaveClass(/active/)
  })
})
