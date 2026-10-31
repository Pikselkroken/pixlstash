import { test, expect } from '../fixtures/test.js'

// The project scope (#2159), in the sidebar's header slot until #2448 moved it
// under the Library / Folders switch. It replaced the
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

  test('draws places at 32px and People and Sets at the thumbnail size', async ({ page }) => {
    const heights = await page
      .locator('.sidebar-media-list .sidebar-list-item:not(.sidebar-section-scroll *)')
      .evaluateAll((rows) => rows.map((r) => r.getBoundingClientRect().height))
    expect(heights.length).toBeGreaterThan(0)
    for (const h of heights) expect(h).toBe(32)

    // People and Sets take the thumbnail-size setting S at the top level
    // (#2124): one height, S plus --space-3, never under the 32px places.
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

// Below Review, Library | Folders (#2448): Library is the dropdown and the
// People and Sets it narrows, Folders the folders pictures are filed in
// (#2458). Flipping it is a display choice and never moves the grid; Find
// filters names in either.
test.describe('the Library / Folders switch', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('Folders draws where pictures are filed and never moves the grid', async ({
    page,
    sidebar,
    apiContext,
  }) => {
    // The fixture library is flat (nothing in a folder), so the tree the
    // server would read is stood in for: each project's folder holding a set
    // folder, then Global and Unassigned. The real read is tests/
    // test_library_layout.py's.
    const projects = await (await apiContext.get('/api/v1/projects')).json()
    const folders = projects.flatMap((p) => [
      { path: p.name, name: p.name, kind: 'project', entity_id: p.id, project_id: p.id, count: 2 },
      { path: `${p.name}/2024`, name: '2024', kind: 'folder', entity_id: null, project_id: p.id, count: 1 },
    ])
    folders.push(
      { path: 'Global', name: 'Global', kind: 'global', entity_id: null, project_id: null, count: 1 },
      { path: 'Unassigned', name: 'Unassigned', kind: 'unassigned', entity_id: null, project_id: null, count: 3 },
    )
    await page.route(
      (url) => url.pathname === '/api/v1/library/folders',
      (route) => route.fulfill({ json: { folders } }),
    )

    await sidebar.chooseFirstProject()
    await expect.poll(() => page.url()).toContain('/project/')
    const url = page.url()

    await page.getByRole('radio', { name: 'Folders' }).click()
    await expect(sidebar.scopeButton).toBeHidden()
    await expect(page.getByTestId('sidebar-folder-global')).toBeVisible()
    await expect(page.getByTestId('sidebar-folder-unassigned')).toContainText('3')
    const current = page.locator(
      '[data-testid="sidebar-folder-project"][aria-current="page"]',
    )
    await expect(current).toHaveCount(1)
    // The chosen project's branch opens on the folder filed inside it.
    await expect(page.getByTestId('sidebar-folder-folder')).toHaveCount(1)
    expect(page.url()).toBe(url)

    await page.getByTestId('sidebar-folder-unassigned').click()
    await expect.poll(() => page.url()).toContain('/character/UNASSIGNED')
    await expect.poll(() => page.url()).toContain('project=none')

    // The mode is a preference: it survives a reload. Back in Library, the
    // dropdown names Unassigned's scope.
    await page.reload()
    await expect(page.getByRole('radio', { name: 'Folders' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    await page.getByRole('radio', { name: 'Library' }).click()
    await expect(sidebar.scopeButton).toContainText('Not in a project')
  })

  test('Find keeps only matching names and offers a picture search otherwise', async ({
    page,
    sidebar,
  }) => {
    await expect(sidebar.characterItems.first()).toBeVisible()
    const name = (
      await sidebar.characterItems.first().locator('.sidebar-list-label-text').innerText()
    ).trim()
    await sidebar.find.fill(name.slice(0, 3))
    await expect(page.locator('.sidebar-find-line')).toContainText('match')
    await expect(sidebar.characterItems.first().locator('mark')).toBeVisible()

    await sidebar.find.fill('zz-no-such-name-zz')
    await expect(page.locator('.sidebar-find-none')).toContainText(
      'No project, person or set is called',
    )
    await sidebar.find.press('Escape')
    await expect(sidebar.find).toHaveValue('')
  })
})
