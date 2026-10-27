import { test, expect } from '../fixtures/test.js'

// The project scope in the sidebar's header slot (#2159). It replaced the
// Global / Projects / Folders tabs, which were display-only switches; the scope
// is a navigation instead. Choosing a project narrows the grid and the People
// and Sets lists and puts the project in the URL; Any project takes both back.

test.describe('the sidebar project scope', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('narrows the view to a project and back', async ({ page, grid, sidebar }) => {
    await grid.waitForThumbnailLoaded()
    await expect(sidebar.scopeButton).toContainText('Any project')
    const peopleEverywhere = await sidebar.characterItems.count()

    await sidebar.chooseFirstProject()
    await expect.poll(() => page.url()).toContain('/project/')
    await expect(sidebar.scopeButton).not.toContainText('Any project')
    await expect(grid.grid).toBeVisible()
    expect(await sidebar.characterItems.count()).toBeLessThanOrEqual(
      peopleEverywhere,
    )

    await sidebar.openScopeMenu()
    await sidebar.scopeMenu
      .getByRole('menuitemradio', { name: /^Any project/ })
      .click()
    await expect.poll(() => page.url()).not.toContain('/project/')
    await expect(sidebar.scopeButton).toContainText('Any project')
  })

  test('keeps Not in a project across a reload', async ({ page, sidebar }) => {
    await sidebar.openScopeMenu()
    await sidebar.scopeMenu
      .getByRole('menuitemradio', { name: /^Not in a project/ })
      .click()
    await expect.poll(() => page.url()).toContain('project=none')
    await page.reload()
    await expect(sidebar.scopeButton).toContainText('Not in a project')
  })

  test('draws every list row at one height', async ({ page }) => {
    const heights = await page
      .locator('.sidebar-media-list .sidebar-list-item')
      .evaluateAll((rows) => rows.map((r) => r.getBoundingClientRect().height))
    expect(heights.length).toBeGreaterThan(0)
    for (const h of heights) expect(h).toBe(32)
  })
})
