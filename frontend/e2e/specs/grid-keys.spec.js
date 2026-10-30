import { test, expect } from '../fixtures/test.js'

// The grid's keys (useGridKeyboardNav.js, and Home/End/PageUp/PageDown routed
// through useGlobalKeydown.js), pressed for real against the real grid. The
// cursor tile must hold the keyboard focus, visibly, after every key that
// moves it; and every key pressed inside a text field must type, not act.

/**
 * The picture id a card shows, from its thumbnail URL. A tile can re-render
 * with its placeholder for a moment, so wait for a real one.
 */
async function cardId(card) {
  const img = card.locator('img.thumbnail-img')
  await expect(img).toHaveAttribute('src', /\/thumbnails\/\d+\./)
  return /\/thumbnails\/(\d+)\./.exec(await img.getAttribute('src'))[1]
}

async function score(apiContext, id) {
  const res = await apiContext.get(`/api/v1/pictures/${id}/metadata`)
  expect(res.ok()).toBe(true)
  return Number((await res.json()).score ?? 0)
}

async function setScore(apiContext, id, value) {
  const res = await apiContext.post('/api/v1/pictures/apply-scores', {
    data: { scores: { [String(id)]: value }, only_unscored: false },
  })
  expect(res.ok()).toBe(true)
}

test.describe('grid keys', () => {
  test.beforeEach(async ({ apiContext, grid }) => {
    // Paging needs a grid taller than the window; pin a size that is.
    const res = await apiContext.patch('/api/v1/users/me/config', {
      data: { thumbnail_size_level: 3, thumbnail_mode: 'square' },
    })
    expect(res.ok()).toBe(true)
    await grid.goto()
  })

  const cursor = (page) => page.locator('.image-card.image-card-cursor')

  /** The cursor tile is the focused element and shows a focus ring. */
  async function expectCursorFocused(page, message) {
    await expect(cursor(page), message).toHaveCount(1)
    await expect
      .poll(
        () =>
          cursor(page).evaluate(
            (el) => el === document.activeElement && el.matches(':focus-visible'),
          ),
        { message },
      )
      .toBe(true)
  }

  test('G, the arrows, Space and Enter drive the cursor with focus visible', async ({
    page,
    grid,
    lightbox,
  }) => {
    await page.keyboard.press('g')
    await expectCursorFocused(page, 'G puts the cursor on the first visible tile')
    await expect(grid.selectionCountLabel).toHaveText('1 selected')
    const first = await cardId(cursor(page))
    expect(first).toBe(await cardId(grid.cards.first()))

    await page.keyboard.press('ArrowRight')
    await expectCursorFocused(page, 'ArrowRight keeps the focus on the cursor')
    const second = await cardId(cursor(page))
    expect(second).not.toBe(first)
    await expect(grid.selectionCountLabel).toHaveText('1 selected')

    // Ctrl+Arrow moves the cursor alone; Space then adds the tile it is on.
    await page.keyboard.press('Control+ArrowRight')
    await expectCursorFocused(page, 'Ctrl+ArrowRight keeps the focus on the cursor')
    const third = await cardId(cursor(page))
    await expect(grid.selectionCountLabel).toHaveText('1 selected')
    await page.keyboard.press(' ')
    await expect(grid.selectionCountLabel).toHaveText('2 selected')
    await expectCursorFocused(page, 'Space leaves the focus on the cursor')
    // And again takes it out.
    await page.keyboard.press(' ')
    await expect(grid.selectionCountLabel).toHaveText('1 selected')

    await page.keyboard.press('Enter')
    await expect(lightbox.root).toBeVisible()
    // `?overlay=` is written by a `replace` that can land after the lightbox shows.
    await expect.poll(() => new URL(page.url()).searchParams.get('overlay')).toBe(third)
  })

  test('Home, End, PageDown and PageUp scroll the grid', async ({ page, grid }) => {
    const top = () => grid.scrollWrapper.evaluate((el) => el.scrollTop)
    const view = await grid.scrollWrapper.evaluate((el) => el.clientHeight)
    expect(
      await grid.scrollWrapper.evaluate((el) => el.scrollHeight),
      'the grid must be more than two windows tall',
    ).toBeGreaterThan(2 * view)
    // Nothing focused, so these are the page's keys and not a tile's.
    await page.evaluate(() => document.activeElement?.blur())

    // End lands on the last row: the last tile is wholly in the window.
    await page.keyboard.press('End')
    await expect.poll(top).toBeGreaterThan(view)
    const bottom = await top()
    const lastInView = () =>
      page.evaluate(() => {
        const wrap = document.querySelector('.grid-scroll-wrapper').getBoundingClientRect()
        const cards = [...document.querySelectorAll('.image-card')]
        const last = cards[cards.length - 1].getBoundingClientRect()
        return last.top >= wrap.top && last.bottom <= wrap.bottom + 1
      })
    await expect.poll(lastInView, { message: 'End must show the whole last row' }).toBe(true)

    await page.keyboard.press('Home')
    await expect.poll(top).toBe(0)
    // A page is one window.
    await page.keyboard.press('PageDown')
    await expect.poll(top).toBe(view)
    await page.keyboard.press('PageDown')
    const twoDown = Math.min(2 * view, bottom)
    await expect.poll(top).toBe(twoDown)
    await page.keyboard.press('PageUp')
    await expect.poll(top).toBe(twoDown - view)
  })

  test('Ctrl+A selects every picture in the view', async ({ page, grid, apiContext }) => {
    const res = await apiContext.get('/api/v1/pictures/count?stack_leaders_only=true')
    const leaders = Number((await res.json()).count)
    await page.keyboard.press('g')
    await page.keyboard.press('ControlOrMeta+a')
    // Every picture in the view, not just the tiles on screen.
    await expect(grid.selectionCountLabel).toHaveText(`${leaders} selected`)
    await page.keyboard.press('Escape')
    await expect(grid.selectionCountLabel).toBeHidden()
  })

  test('0–5 rate the cursor picture', async ({ page, apiContext }) => {
    await page.keyboard.press('g')
    await expectCursorFocused(page, 'G puts the cursor on a tile')
    const id = await cardId(cursor(page))
    // The backend is shared by the whole run: put back what the picture had.
    const before = await score(apiContext, id)
    await setScore(apiContext, id, 0)
    try {
      for (const n of [3, 5, 1, 0]) {
        await page.keyboard.press(String(n))
        await expect.poll(() => score(apiContext, id), { message: `key ${n}` }).toBe(n)
        await expectCursorFocused(page, `key ${n} leaves the focus on the cursor`)
      }
    } finally {
      await setScore(apiContext, id, before)
    }
  })

  test('every grid key types into a text field and does nothing else', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    // The pointer rests on a tile: a digit that leaked would rate it (a bare
    // digit rates the hovered picture), G would put the cursor on the grid,
    // Space or Ctrl+A would select. F puts the caret in the search field
    // without moving the pointer off the tile.
    const tile = grid.cards.nth(1)
    const id = await cardId(tile)
    const before = await score(apiContext, id)
    await setScore(apiContext, id, 0)
    try {
      await tile.hover()
      const scrolled = await grid.scrollWrapper.evaluate((el) => el.scrollTop)
      await page.keyboard.press('f')
      await expect(grid.searchInput).toBeFocused()

      const typed = 'g 012345t'
      for (const key of typed) await page.keyboard.press(key === ' ' ? 'Space' : key)
      await expect(grid.searchInput).toHaveValue(typed)

      // Ctrl+A is the field's own select-all; Home/End/Page keys move its caret.
      for (const key of ['ControlOrMeta+a', 'Home', 'End', 'PageDown', 'PageUp']) {
        await page.keyboard.press(key)
      }
      await expect(grid.searchInput).toHaveValue(typed)

      await expect(cursor(page)).toHaveCount(0)
      await expect(grid.selectionCountLabel).toBeHidden()
      await expect(lightbox.root).toBeHidden()
      expect(await grid.scrollWrapper.evaluate((el) => el.scrollTop)).toBe(scrolled)
      expect(await score(apiContext, id)).toBe(0)

      await grid.searchInput.fill('')
      await page.keyboard.press('Escape')
    } finally {
      await setScore(apiContext, id, before)
    }
  })
})
