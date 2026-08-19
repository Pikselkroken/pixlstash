import { test, expect } from '../fixtures/test.js'

// Phase 0 characterization pin — STATELESS SIDEBAR TABS.
// (frontend_architecture.md §5 "Sidebar tabs are stateless"; frontend_refactoring
// plan Phase 0; issue #459 alignment rule 3.)
//
// The invariant: switching a sidebar view tab (Global / Projects / Folders) is a
// pure sidebar-display operation. It must NOT push a route and must NOT move the
// grid — the grid keeps showing whatever the current route resolves to. This is
// what lets a user find pictures on a global view, switch to the Projects tab
// purely to reveal drop targets, and drag onto them without losing the view.
//
// This pins CURRENT behaviour before Phase 3/4 rewire the sidebar, so the
// documented anti-pattern (a tab watch that navigates / resets a filter) cannot
// sneak back in. Runnable now, but needs the live e2e app (Playwright + backend).

function idFromThumbSrc(src) {
  const m = /\/thumbnails\/(\d+)\./.exec(src || '')
  return m ? Number(m[1]) : null
}

test.describe('sidebar tabs are stateless (route is the single source of truth)', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('switching view tabs changes neither the route nor the grid', async ({
    page,
    grid,
    sidebar,
  }) => {
    await grid.waitForThumbnailLoaded()
    const urlBefore = page.url()
    const firstBefore = await grid.firstThumbnailKey()
    expect(idFromThumbSrc(firstBefore)).not.toBeNull()

    // Global → Projects: sidebar reveals the project tree; grid untouched.
    await sidebar.projectsTab.click()
    await expect(sidebar.projectRows.first()).toBeVisible()
    expect(page.url(), 'Projects tab must not push a route').toBe(urlBefore)
    expect(
      await grid.firstThumbnailKey(),
      'Projects tab must not move the grid',
    ).toBe(firstBefore)

    // Projects → Folders: sidebar shows folders; grid still untouched.
    await sidebar.foldersTab.click()
    await expect(sidebar.foldersTab).toHaveClass(/active/)
    expect(page.url(), 'Folders tab must not push a route').toBe(urlBefore)
    expect(
      await grid.firstThumbnailKey(),
      'Folders tab must not move the grid',
    ).toBe(firstBefore)

    // Folders → Global: back to the start; still no navigation, still same grid.
    await sidebar.globalTab.click()
    await expect(sidebar.globalTab).toHaveClass(/active/)
    expect(page.url(), 'Global tab must not push a route').toBe(urlBefore)
    expect(await grid.firstThumbnailKey()).toBe(firstBefore)
  })

  test('a tab switch does not disturb an active entity view', async ({
    page,
    grid,
    sidebar,
  }) => {
    // Navigate into a set via an entry click (this IS the only navigation).
    await expect(sidebar.setItems.first()).toBeVisible()
    const row = await sidebar.firstNonEmpty(sidebar.setItems)
    await row.click()
    await expect.poll(() => page.url()).toContain('/set/')
    await grid.waitForThumbnailLoaded()
    const setUrl = page.url()
    const setFirst = await grid.firstThumbnailKey()

    // Switch to Projects then Folders: the set view underneath is preserved.
    await sidebar.projectsTab.click()
    await expect(sidebar.projectRows.first()).toBeVisible()
    expect(page.url(), 'switching tabs must not leave the set route').toBe(setUrl)
    expect(await grid.firstThumbnailKey()).toBe(setFirst)

    await sidebar.foldersTab.click()
    await expect(sidebar.foldersTab).toHaveClass(/active/)
    expect(page.url()).toBe(setUrl)
    expect(await grid.firstThumbnailKey()).toBe(setFirst)
  })
})
