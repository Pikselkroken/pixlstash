import { test, expect } from '../fixtures/test.js'

// The Media sidebar's tree (#2448). All media, Favourites, Scrapheap and Review
// are global; below them a Library / Folders switch draws the rest. A project
// row is "all of that project" and opens in place; Not in a project has
// `?project=none`. Find in the header slot filters the tree by name.

test.describe('the sidebar tree', () => {
  test.beforeEach(async ({ page, grid }) => {
    // This spec is about the tree itself, so it starts the way a person's
    // first visit does: every branch closed (the fixture opens them).
    await page.addInitScript(() => {
      window.localStorage.setItem('pixlstash:sidebar:tree', '[]')
    })
    await grid.goto()
  })

  test('a project row opens all of it in place, and All media takes it back', async ({
    page,
    grid,
    sidebar,
  }) => {
    await grid.waitForThumbnailLoaded()
    await expect(sidebar.allMedia).toHaveAttribute('aria-current', 'page')
    const project = sidebar.projectRows.first()
    await expect(project).toHaveAttribute('aria-expanded', 'false')

    await sidebar.chooseFirstProject()
    await expect.poll(() => page.url()).toContain('/project/')
    await expect(project).toHaveAttribute('aria-expanded', 'true')
    await expect(sidebar.allMedia).not.toHaveAttribute('aria-current', 'page')
    await expect(grid.grid).toBeVisible()

    await sidebar.allMedia.click()
    await expect.poll(() => page.url()).not.toContain('/project/')
    await expect(sidebar.allMedia).toHaveAttribute('aria-current', 'page')
  })

  test('keeps Not in a project across a reload', async ({ page, sidebar }) => {
    await sidebar.noProjectRow.click()
    await expect.poll(() => page.url()).toContain('project=none')
    await page.reload()
    await expect(sidebar.noProjectRow).toHaveAttribute('aria-current', 'page')
  })

  test('Folders draws Global and Unassigned, and flipping it never moves the grid', async ({
    page,
    sidebar,
  }) => {
    await sidebar.chooseFirstProject()
    const url = page.url()

    await page.getByRole('radio', { name: 'Folders' }).click()
    await expect(page.getByTestId('sidebar-folder-global')).toBeVisible()
    await expect(page.getByTestId('sidebar-folder-unassigned')).toBeVisible()
    await expect(
      page.locator('[data-testid="sidebar-folder-project"][aria-current="page"]'),
    ).toHaveCount(1)
    expect(page.url()).toBe(url)

    await page.getByTestId('sidebar-folder-unassigned').click()
    await expect.poll(() => page.url()).toContain('/character/UNASSIGNED')
    await expect.poll(() => page.url()).toContain('project=none')

    // The mode is a preference: it survives a reload.
    await page.reload()
    await expect(page.getByRole('radio', { name: 'Folders' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    await page.getByRole('radio', { name: 'Library' }).click()
  })

  test('Find keeps only what matches, and offers a picture search otherwise', async ({
    page,
    sidebar,
  }) => {
    const name = (await sidebar.projectRows.first().innerText()).split('\n')[0].trim()
    await sidebar.find.fill(name.slice(0, 3))
    await expect(page.locator('.sidebar-find-line')).toContainText('match')
    await expect(sidebar.projectRow(name).locator('mark')).toBeVisible()

    await sidebar.find.fill('zz-no-such-name-zz')
    await expect(page.locator('.sidebar-find-none')).toContainText(
      'No project, person or set is called',
    )
    await sidebar.find.press('Escape')
    await expect(sidebar.find).toHaveValue('')
  })

  test('draws places at 32px and People and Sets at the thumbnail size', async ({
    page,
    sidebar,
  }) => {
    const heights = await page
      .locator('.sidebar-media-list .sidebar-list-item:not(.sidebar-section-scroll *)')
      .evaluateAll((rows) => rows.map((r) => r.getBoundingClientRect().height))
    expect(heights.length).toBeGreaterThan(0)
    for (const h of heights) expect(h).toBe(32)

    // People and Sets take the thumbnail-size setting S at the top level
    // (#2124): one height, S plus --space-3, never under the 32px places.
    await sidebar.noProjectRow.locator('.sidebar-branch-toggle').click()
    await sidebar.chooseFirstProject()
    const { thumb, entityHeights } = await page.evaluate(() => {
      const section = document.querySelector('.sidebar-media-list .sidebar-section-scroll')
      const rows = document.querySelectorAll(
        '.sidebar-media-list .sidebar-section-scroll .sidebar-list-item:not(.sidebar-tree-kid)',
      )
      return {
        thumb: parseFloat(getComputedStyle(section).getPropertyValue('--sidebar-thumb-size')),
        entityHeights: [...rows].map((r) => r.getBoundingClientRect().height),
      }
    })
    expect(entityHeights.length).toBeGreaterThan(0)
    for (const h of entityHeights) expect(h).toBe(Math.max(32, thumb + 8))
  })
})
