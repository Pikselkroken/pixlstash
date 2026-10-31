import { test, expect } from '../fixtures/test.js'

// Deleting from the lightbox moves on to the next picture instead of closing
// (#2346). Each test works in a set of three unstacked pictures it makes
// itself, so "the view" has a known order and a known end, and puts back what
// it sent to the Scrapheap. Delete forever is driven to its confirm and
// cancelled, with the purge blocked at the network (as in scrapheap.spec.js).

const API = '/api/v1'

const receipt = (page) => page.locator('[data-testid="lightbox-action-receipt"]')
const position = (page) => page.locator('.lightbox-position')

/** The picture on screen. Arrow keys change it without rewriting the URL. */
async function shownPictureId(lightbox) {
  const src = await lightbox.mainImage.getAttribute('src')
  return Number(/\/pictures\/(\d+)[./?]/.exec(src || '')?.[1] ?? NaN)
}

async function scrapheapIds(apiContext) {
  const listed = await apiContext.get(`${API}/pictures`, {
    params: { only_deleted: 'true', fields: 'grid' },
  })
  expect(listed.ok(), await listed.text()).toBe(true)
  return (await listed.json()).map((picture) => picture.id)
}

/** The set's cards in the grid's order, which is the lightbox's order. */
async function gridOrder(grid) {
  await grid.waitForLoaded()
  const srcs = await grid.thumbnailImages.evaluateAll((imgs) =>
    imgs.map((img) => img.getAttribute('src')),
  )
  return [...new Set(srcs.map((src) => Number(src?.match(/\/thumbnails\/(\d+)\./)?.[1])))]
}

/** Take the keyboard off whatever button the open left focused. */
function releaseFocus(page) {
  return page.evaluate(() => document.activeElement?.blur())
}

test.describe('deleting from the lightbox', () => {
  let setId = null
  let heapBefore = []

  test.beforeEach(async ({ apiContext }) => {
    heapBefore = await scrapheapIds(apiContext)
    const listed = await apiContext.get(`${API}/pictures`, { params: { fields: 'grid' } })
    const ids = (await listed.json())
      .filter((picture) => !picture.stack_id && !picture.video)
      .slice(0, 3)
      .map((picture) => picture.id)
    expect(ids.length, 'the fixture has three unstacked pictures').toBe(3)
    const created = await apiContext.post(`${API}/picture_sets`, {
      data: { name: `e2e lightbox delete ${Date.now()}` },
    })
    expect(created.ok(), await created.text()).toBe(true)
    const set = await created.json()
    setId = set.id ?? set.picture_set?.id
    const added = await apiContext.post(`${API}/picture_sets/${setId}/members`, {
      data: { picture_ids: ids },
    })
    expect(added.ok(), await added.text()).toBe(true)
  })

  test.afterEach(async ({ page, apiContext }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    const mine = (await scrapheapIds(apiContext)).filter((id) => !heapBefore.includes(id))
    if (mine.length) {
      const restored = await apiContext.post(`${API}/pictures/scrapheap/restore`, {
        data: { picture_ids: mine },
      })
      expect(restored.ok(), await restored.text()).toBe(true)
    }
    if (setId != null) {
      const removed = await apiContext.delete(`${API}/picture_sets/${setId}`)
      expect(removed.ok(), await removed.text()).toBe(true)
      setId = null
    }
  })

  /** Open the set's grid and the lightbox on the picture at `index`. */
  async function openAt(page, grid, lightbox, index) {
    await page.goto(`/set/${setId}`)
    const order = await gridOrder(grid)
    expect(order).toHaveLength(3)
    await lightbox.openFromGrid(grid.card(order[index]).locator('.thumbnail-card'))
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[index])
    await releaseFocus(page)
    return order
  }

  test('Delete moves on to the next picture, says so, and blocks nothing', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    const order = await openAt(page, grid, lightbox, 0)

    await page.keyboard.press('Delete')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[1])
    await expect(lightbox.root).toBeVisible()
    await expect(position(page)).toHaveText(/1 of 2/)
    await expect.poll(() => scrapheapIds(apiContext)).toContain(order[0])

    // The receipt says where it went, and the next key works at once without
    // dismissing it.
    await expect(receipt(page)).toBeVisible()
    await expect(receipt(page)).toContainText(/Scrapheap/i)
    await page.keyboard.press('ArrowRight')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[2])
    await expect(receipt(page)).toBeVisible()
    await page.keyboard.press('ArrowLeft')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[1])

    // It goes by itself (the destructive 8s dwell), and the tile behind goes
    // with it, as after a grid delete.
    await expect(receipt(page)).toHaveCount(0, { timeout: 15_000 })
    await page.keyboard.press('Escape')
    await expect(lightbox.root).toHaveCount(0)
    await expect(grid.card(order[0])).toHaveCount(0)
    await expect(grid.card(order[1])).toBeVisible()
  })

  test('Ctrl+Z brings the deleted picture back into the lightbox', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    const order = await openAt(page, grid, lightbox, 0)
    await page.keyboard.press('Delete')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[1])
    await expect(receipt(page)).toBeVisible()

    await page.keyboard.press('ControlOrMeta+z')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[0])
    await expect(position(page)).toHaveText(/1 of 3/)
    await expect.poll(() => scrapheapIds(apiContext)).not.toContain(order[0])
    // And it is back in the order: right steps to the picture after it.
    await page.keyboard.press('ArrowRight')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[1])
  })

  test('at the end it steps back, and the last picture closes the lightbox', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    const order = await openAt(page, grid, lightbox, 2)

    // A held key: one delete at a time, each step shown, one receipt only.
    await page.keyboard.press('Delete')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[1])
    await expect(position(page)).toHaveText(/2 of 2/)
    await page.keyboard.press('Delete')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[0])
    await expect(receipt(page)).toHaveCount(1)

    await page.keyboard.press('Delete')
    await expect(lightbox.root).toHaveCount(0)
    await expect.poll(async () => {
      const heap = await scrapheapIds(apiContext)
      return order.every((id) => heap.includes(id))
    }).toBe(true)
  })

  test('the next picture slides in, and only fades under reduced motion', async ({
    page,
    grid,
    lightbox,
  }) => {
    const animation = () =>
      page.locator('.lightbox-media-inner').evaluate((el) => getComputedStyle(el).animationName)

    await page.emulateMedia({ reducedMotion: 'no-preference' })
    const order = await openAt(page, grid, lightbox, 0)
    // Before any delete there is nothing to play.
    expect(await animation()).toBe('none')
    await page.keyboard.press('Delete')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[1])
    // Scoped keyframes carry a suffix: the slide, not the fade.
    expect(await animation()).toMatch(/^lightbox-arrive(?!-fade)/)

    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.keyboard.press('Delete')
    await expect.poll(() => shownPictureId(lightbox)).toBe(order[2])
    expect(await animation()).toMatch(/^lightbox-arrive-fade/)
  })

  test('in the Scrapheap, Delete asks first and Cancel stays on the picture', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    await page.goto(`/set/${setId}`)
    const order = await gridOrder(grid)
    const deleted = await apiContext.delete(`${API}/pictures`, {
      data: { picture_ids: order },
    })
    expect(deleted.ok(), await deleted.text()).toBe(true)
    const purges = []
    await page.route(
      (url) => url.pathname === `${API}/pictures/scrapheap`,
      (route) => {
        if (route.request().method() === 'DELETE') {
          purges.push(route.request().url())
          return route.abort()
        }
        return route.continue()
      },
    )

    await page.goto('/scrapheap')
    await grid.waitForLoaded()
    await lightbox.openFromGrid(grid.card(order[0]).locator('.thumbnail-card'))
    await releaseFocus(page)
    const shown = await shownPictureId(lightbox)

    await page.keyboard.press('Delete')
    const dialog = page.locator('.v-overlay--active.v-dialog', { hasText: 'Delete forever?' })
    await expect(dialog).toBeVisible()
    // Keys belong to the confirm: an arrow must not move the picture it names.
    await page.keyboard.press('ArrowRight')
    expect(await shownPictureId(lightbox)).toBe(shown)
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toHaveCount(0)

    await expect(lightbox.root).toBeVisible()
    expect(await shownPictureId(lightbox)).toBe(shown)
    expect(purges).toEqual([])
    expect(await scrapheapIds(apiContext)).toContain(shown)
  })
})
