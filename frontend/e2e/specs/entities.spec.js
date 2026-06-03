import { test, expect } from '../fixtures/test.js'

// Release plan §7/§8/§9 — Picture Sets, Projects, and Characters. Clicking an
// entity in the sidebar navigates to its route (/set, /project, /character) and
// filters the grid to it. The grid virtualises, so we assert deterministic
// signals (route change, the row's active state, thumbnails still render) rather
// than exact thumbnail counts. Navigation only — no data is mutated.

test.describe('entity navigation', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('filters the grid to a picture set (§7)', async ({ page, grid, sidebar }) => {
    await expect(sidebar.setItems.first()).toBeVisible()
    const row = await sidebar.firstNonEmpty(sidebar.setItems)

    await row.click()
    await expect.poll(() => page.url()).toContain('/set/')
    await expect(row).toHaveClass(/active/)
    await expect(grid.thumbnails.first()).toBeVisible()
  })

  test('filters the grid to a character (§9)', async ({ page, grid, sidebar }) => {
    await expect(sidebar.characterItems.first()).toBeVisible()
    const row = await sidebar.firstNonEmpty(sidebar.characterItems)

    await row.click()
    await expect.poll(() => page.url()).toContain('/character/')
    await expect(row).toHaveClass(/active/)
    await expect(grid.thumbnails.first()).toBeVisible()
  })

  test('opens a project from the Projects tab (§8)', async ({ page, grid, sidebar }) => {
    await sidebar.openProjectsTab()
    const row = sidebar.projectRows.first()
    await expect(row).toBeVisible()

    await row.click()
    await expect.poll(() => page.url()).toContain('/project/')
    // A project view still renders the grid; some seeded projects may be empty,
    // so we assert the grid container is present rather than a thumbnail count.
    await expect(grid.grid).toBeVisible()
  })
})
