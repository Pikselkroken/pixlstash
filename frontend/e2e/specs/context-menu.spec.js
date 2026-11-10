import { test, expect } from '../fixtures/test.js'

// Release plan §3.5 — the right-click context menu. Right-clicking a grid card
// opens the menu and exposes the selection-scoped actions (Tag, Share image, the
// tool groups such as Find & compare …), and the non-destructive ones are run
// here and their result checked: Set ▸ adds and removes, Tag… opens the tag
// field, Compare lays two pictures over each other. Delete is covered by #2308,
// Share image by sharing.spec.js, Move to library by move-to-library.spec.js.
//
// Set ▸ works on a set this spec makes and deletes, with a loose picture: a
// stacked one would take its whole stack into the set.

/** Picture id of a grid card, read off its thumbnail URL. */
async function cardPictureId(card) {
  const src = await card.locator('img.thumbnail-img').getAttribute('src')
  const id = Number(src?.match(/\/thumbnails\/(\d+)\./)?.[1])
  expect(Number.isFinite(id), `not a thumbnail URL: ${src}`).toBe(true)
  return id
}

test.describe('context menu', () => {
  let set = null

  test.beforeAll(async ({ apiContext }) => {
    const created = await apiContext.post('/api/v1/picture_sets', {
      data: { name: `e2e-ctx-${Date.now().toString(36)}` },
    })
    expect(created.ok(), await created.text()).toBe(true)
    set = (await created.json()).picture_set
  })

  test.afterAll(async ({ apiContext }) => {
    if (!set) return
    const res = await apiContext.delete(`/api/v1/picture_sets/${set.id}`)
    expect(res.ok(), await res.text()).toBe(true)
  })

  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('opens on right-click and lists picture actions (§3.5)', async ({ page, grid }) => {
    await grid.openContextMenu()

    await expect(grid.contextMenuItem('Tag')).toBeVisible()
    await expect(grid.contextMenuItem('Share image')).toBeVisible()
    // The tools fold into groups: Reverse image search is behind Find & compare.
    await grid.contextMenuItem('Find & compare').hover()
    await expect(grid.contextMenuItem('Reverse image search')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(grid.contextMenu).toBeHidden()
  })

  test('Set ▸ adds the picture to the set picked, and picking it again removes it', async ({
    page,
    grid,
    apiContext,
  }) => {
    const listed = await apiContext.get('/api/v1/pictures', {
      params: { fields: 'grid', limit: 1000 },
    })
    expect(listed.ok(), await listed.text()).toBe(true)
    const loose = new Set(
      (await listed.json()).filter((p) => p.stack_id == null).map((p) => p.id),
    )
    let pictureId = null
    for (let i = 0; pictureId == null && i < 20; i++) {
      const id = await cardPictureId(grid.thumbnails.nth(i))
      if (loose.has(id)) pictureId = id
    }
    expect(pictureId, 'a loose picture near the top of the grid').not.toBeNull()

    const members = async () => {
      const res = await apiContext.get(`/api/v1/picture_sets/${set.id}/members`)
      expect(res.ok(), await res.text()).toBe(true)
      return (await res.json()).picture_ids
    }
    const pickSet = async () => {
      await grid.openContextMenu(grid.card(pictureId))
      await grid.contextMenu.locator('.ate-btn', { hasText: /^\s*Set\s*$/ }).click()
      // A set just used is listed twice (pinned, and under Other sets); either
      // row toggles the same membership.
      await page.locator('.ate-menu').getByRole('option', { name: set.name }).first().click()
      // Escape shuts the flyout first, then the menu.
      await expect(async () => {
        await page.keyboard.press('Escape')
        await expect(grid.contextMenu).toBeHidden({ timeout: 1_000 })
      }).toPass()
    }

    expect(await members()).toEqual([])
    await pickSet()
    await expect.poll(members).toEqual([pictureId])
    await pickSet()
    await expect.poll(members).toEqual([])
  })

  test('Tag… opens and focuses the tag field', async ({ page, grid }) => {
    await grid.openContextMenu()
    await grid.contextMenuItem('Tag').click()
    await expect(grid.contextMenu).toBeHidden()
    // The field lives in the stats sidebar, which the action opens.
    await expect(grid.statsSidebar).toBeVisible()
    await expect(page.locator('.stats-sidebar .rail-tags__input')).toBeFocused()
  })

  test('Find & compare ▸ Compare lays the two selected pictures over each other', async ({
    page,
    grid,
  }) => {
    const first = await cardPictureId(grid.thumbnails.nth(0))
    const second = await cardPictureId(grid.thumbnails.nth(1))
    await grid.selectCards(2)

    await grid.openContextMenuOnSelected(0)
    await grid.contextMenuItem('Find & compare').hover()
    // Not contextMenuItem('Compare'): its substring match lands on "Find &
    // compare" itself.
    await grid.contextMenu.locator('.ctx-item', { hasText: /^\s*Compare\s*$/ }).click()

    // One picture on the stage, the other clipped over it at the slider.
    await expect(page.locator('.paint-compare')).toBeVisible()
    const shown = [
      await page.locator('.paint-compare-before').getAttribute('src'),
      await page.locator('.lightbox-media-inner img').first().getAttribute('src'),
    ].map((src) => Number(src?.match(/\/(\d+)\.[a-z0-9]+(?:\?|$)/i)?.[1]))
    expect(shown.sort()).toEqual([first, second].sort())
  })
})
