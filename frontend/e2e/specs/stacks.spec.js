import { test, expect } from '../fixtures/test.js'

// Release plan §3.4 / §11 — Stacks. The fixture has 10 stacks. Driving the
// per-thumbnail badge is occluded by the stats sidebar at the right edge, so
// the expand/collapse test goes through the View menu's "Expand all" /
// "Collapse all" controls, asserting on the .image-card-stack-expanded class
// the grid applies to expanded leaders. Read-only.
//
// The editing test changes a stack's order and membership, so it works on a
// stack of its own and takes it apart afterwards. Its pictures are loose ones
// in no set and no project: stacks are atomic for set and project membership,
// so stacking a picture that has either would hand it to its new siblings,
// and taking the stack apart again would not give it back.

test.describe('stacks', () => {
  test('expands and collapses stacks from the View menu', async ({ page, grid }) => {
    await grid.goto()
    const expandedCards = page.locator('.image-card-stack-expanded')

    await grid.openViewMenu()
    await grid.expandAllStacksButton.click()
    await expect(expandedCards.first()).toBeVisible()

    await grid.collapseAllStacksButton.click()
    await expect(expandedCards).toHaveCount(0)
  })

  test.describe('editing a stack', () => {
    let stackId = null
    let members = []

    test.afterAll(async ({ apiContext }) => {
      if (!stackId) return
      // Whatever the test got to: take apart what is left of its stack, which
      // deletes the stack and leaves every picture loose again.
      const left = await apiContext.get(`/api/v1/stacks/${stackId}/pictures`)
      if (left.status() === 404) return
      expect(left.ok(), await left.text()).toBe(true)
      const res = await apiContext.delete(`/api/v1/stacks/${stackId}/members`, {
        data: { picture_ids: (await left.json()).map((p) => p.id) },
      })
      expect(res.ok(), await res.text()).toBe(true)
    })

    /**
     * Stack four loose pictures from the top of the grid, so every card is on
     * screen without scrolling.
     */
    async function makeStack(page, grid, apiContext) {
      await grid.goto()
      await expect(page.locator('.thumbnail-img').nth(20)).toBeVisible({ timeout: 15_000 })
      const onScreen = (
        await page
          .locator('.thumbnail-img')
          .evaluateAll((imgs) => imgs.map((img) => img.getAttribute('src') || ''))
      )
        .map((src) => Number(src.match(/\/thumbnails\/(\d+)\./)?.[1]))
        .filter(Number.isFinite)

      const listed = await apiContext.get('/api/v1/pictures', {
        params: { fields: 'grid', project_id: 'UNASSIGNED', limit: 1000 },
      })
      expect(listed.ok(), await listed.text()).toBe(true)
      const loose = new Set(
        (await listed.json()).filter((p) => p.stack_id == null).map((p) => p.id),
      )
      const inSets = await apiContext.post('/api/v1/picture_sets/membership', {
        data: { picture_ids: onScreen },
      })
      expect(inSets.ok(), await inSets.text()).toBe(true)
      const setMembers = new Set(Object.values(await inSets.json()).flat())
      members = onScreen.filter((id) => loose.has(id) && !setMembers.has(id)).slice(0, 4)
      expect(members, 'four loose pictures at the top of the grid').toHaveLength(4)

      const created = await apiContext.post('/api/v1/stacks', {
        data: { picture_ids: members },
      })
      expect(created.ok(), await created.text()).toBe(true)
      stackId = (await created.json()).id
      expect(stackId).toBeTruthy()
    }

    /** The stack's order as the server holds it. */
    async function serverOrder(apiContext) {
      const res = await apiContext.get(`/api/v1/stacks/${stackId}/pictures`)
      expect(res.ok(), await res.text()).toBe(true)
      return (await res.json()).map((p) => p.id)
    }

    /** Open the stack whose cover is `coverId` from its context menu. */
    async function openStack(grid, coverId) {
      await grid.openContextMenu(grid.card(coverId))
      await grid.contextMenuItem('Edit stack').hover()
      await grid.contextMenu.getByRole('menuitem', { name: 'Open stack' }).click()
      await expect(grid.card(coverId).locator('.stack-position-chip')).toBeVisible()
    }

    /** Run one Edit stack ▸ action on an open stack's member. */
    async function editStack(grid, pictureId, action) {
      await grid.openContextMenu(grid.card(pictureId))
      await grid.contextMenuItem('Edit stack').hover()
      await grid.contextMenu.getByRole('menuitem', { name: action }).click()
    }

    /** Each picture's place, as the grid's position chips show it. */
    async function expectChips(grid, order) {
      for (const [i, id] of order.entries()) {
        await expect(grid.card(id).locator('.stack-position-chip')).toHaveText(String(i + 1))
      }
    }

    test('make a cover, take a member out and reorder in the filmstrip; each survives a reload', async ({
      page,
      grid,
      lightbox,
      apiContext,
    }) => {
      await makeStack(page, grid, apiContext)
      // The server picks the starting order; every step below moves from it.
      const [m0, m1, m2, m3] = await serverOrder(apiContext)
      expect([m0, m1, m2, m3].sort()).toEqual([...members].sort())

      await grid.goto()
      await openStack(grid, m0)
      await expectChips(grid, [m0, m1, m2, m3])

      // Make the third picture the cover.
      await editStack(grid, m2, /^Make cover/)
      await expectChips(grid, [m2, m0, m1, m3])
      await expect.poll(() => serverOrder(apiContext)).toEqual([m2, m0, m1, m3])
      await page.reload()
      await grid.waitForLoaded()
      await openStack(grid, m2)
      await expectChips(grid, [m2, m0, m1, m3])

      // Take the last one out: it becomes a loose picture.
      await editStack(grid, m3, /^Take out of stack/)
      await expect.poll(() => serverOrder(apiContext)).toEqual([m2, m0, m1])
      await expect(grid.card(m3).locator('.stack-position-chip')).toHaveCount(0)
      await page.reload()
      await grid.waitForLoaded()
      await expect(grid.card(m3)).toBeVisible()
      await openStack(grid, m2)
      await expectChips(grid, [m2, m0, m1])
      await expect(grid.card(m3).locator('.stack-position-chip')).toHaveCount(0)

      // Reorder from the lightbox filmstrip: move the cover one place later.
      await lightbox.openFromGrid(grid.card(m2).locator('.thumbnail-card'))
      const coverThumb = page.locator('.filmstrip-thumb', {
        has: page.locator(`img[src*="/thumbnails/${m2}."]`),
      })
      await coverThumb.hover()
      await coverThumb.getByRole('button', { name: 'Move later' }).click()
      await expect.poll(() => serverOrder(apiContext)).toEqual([m0, m2, m1])
      // A fresh load of the grid rather than reload(): the URL names the
      // picture the lightbox is on, and reloading it reopens the lightbox.
      await grid.goto()
      await openStack(grid, m0)
      await expectChips(grid, [m0, m2, m1])
    })
  })
})
