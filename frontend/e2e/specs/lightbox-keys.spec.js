import { test, expect } from '../fixtures/test.js'

// The lightbox's own keys (Lightbox.vue's keydown handler): Z, Space, S, A, T
// and 1–5, each checked by what it changes, and each checked again from inside
// the tag field, where it must type and change nothing.
//
// A adds to the set last added to from this lightbox, so the spec makes its
// own set and deletes it afterwards.

const zoomButton = (page) => page.locator('.lightbox .zoom-btn')
const shell = (page) => page.locator('.lightbox-shell')

/**
 * Take the keyboard off whatever button the open left focused, so a key goes
 * to the lightbox and Space is not that button's click. Not a click on the
 * picture: a tap there hides the chrome.
 */
function releaseFocus(page) {
  return page.evaluate(() => document.activeElement?.blur())
}

function openPictureId(page) {
  return new URL(page.url()).searchParams.get('overlay')
}

/** The picture on screen. Arrow keys change it without rewriting the URL. */
async function shownPictureId(lightbox) {
  const src = await lightbox.mainImage.getAttribute('src')
  return /\/pictures\/(\d+)[./?]/.exec(src || '')?.[1] ?? null
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

async function setMembers(apiContext, setId) {
  const res = await apiContext.get(`/api/v1/picture_sets/${setId}/members`)
  expect(res.ok()).toBe(true)
  const body = await res.json()
  const rows = Array.isArray(body) ? body : body.picture_ids || body.members || []
  return rows.map((m) => String(typeof m === 'object' ? (m.picture_id ?? m.id) : m)).sort()
}

test.describe('lightbox keys', () => {
  test.beforeEach(async ({ page, grid, lightbox }) => {
    await grid.goto()
    await lightbox.openFromGrid()
    await releaseFocus(page)
  })

  test('Z zooms to 100% and back to fit', async ({ page, lightbox, apiContext }) => {
    // Fit is the zoom floor, so on a picture smaller than the stage Z has
    // nowhere to go. Use the fixture's tallest picture, which is not.
    const body = await (
      await apiContext.get('/api/v1/pictures?stack_leaders_only=true&fields=grid&limit=500')
    ).json()
    const rows = Array.isArray(body) ? body : body.pictures || body.items || []
    const big = rows.reduce((a, b) => ((b.height || 0) > (a.height || 0) ? b : a))
    expect(big.height, 'the fixture has a picture taller than the window').toBeGreaterThan(720)

    await page.goto(`/?overlay=${big.id}`)
    await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
    await expect(zoomButton(page)).toHaveAttribute('aria-label', /% \(fit\)/)
    await releaseFocus(page)
    await page.keyboard.press('z')
    await expect(zoomButton(page)).toHaveAttribute('aria-label', /^Zoom 100% - click to fit/)
    await page.keyboard.press('z')
    await expect(zoomButton(page)).toHaveAttribute('aria-label', /% \(fit\)/)
  })

  test('Space hides the chrome and Space brings it back', async ({ page }) => {
    await expect(shell(page)).not.toHaveClass(/chrome-hidden/)
    await page.keyboard.press(' ')
    await expect(shell(page)).toHaveClass(/chrome-hidden/)
    await page.keyboard.press(' ')
    await expect(shell(page)).not.toHaveClass(/chrome-hidden/)
  })

  test('S closes and reopens the sidebar', async ({ page, lightbox }) => {
    await expect(lightbox.pane).not.toHaveClass(/inspector--collapsed/)
    await page.keyboard.press('s')
    await expect(lightbox.pane).toHaveClass(/inspector--collapsed/)
    await page.keyboard.press('s')
    await expect(lightbox.pane).not.toHaveClass(/inspector--collapsed/)
  })

  test('T puts the caret in the tag field', async ({ page, lightbox }) => {
    await page.keyboard.press('t')
    await expect(lightbox.tagInput).toBeFocused()
    await page.keyboard.press('Escape')
  })

  test('1–5 rate the picture on screen', async ({ page, lightbox, apiContext }) => {
    const id = openPictureId(page)
    // The backend is shared by the whole run: put back what the picture had.
    const before = await score(apiContext, id)
    await setScore(apiContext, id, 0)
    try {
      for (const n of [4, 2, 5]) {
        await page.keyboard.press(String(n))
        await expect.poll(() => lightbox.filledStarCount()).toBe(n)
        await expect.poll(() => score(apiContext, id), { message: `key ${n}` }).toBe(n)
      }
    } finally {
      await setScore(apiContext, id, before)
    }
  })

  test('A adds the next picture to the set last added to', async ({
    page,
    lightbox,
    apiContext,
  }) => {
    const name = `e2e lightbox A ${Date.now()}`
    const created = await apiContext.post('/api/v1/picture_sets', { data: { name } })
    expect(created.ok(), await created.text()).toBe(true)
    const set = await created.json()
    const setId = set.id ?? set.picture_set?.id
    try {
      // Nothing added yet this lightbox: A has no set to add to.
      await page.keyboard.press('a')
      expect(await setMembers(apiContext, setId)).toEqual([])

      const first = await shownPictureId(lightbox)
      expect(first).toBe(openPictureId(page))
      const setButton = page.locator('.lightbox').getByRole('button', { name: 'Set', exact: true })
      await setButton.click()
      // The menu floats outside the control; the control carries `open`.
      const control = page.locator('.lightbox .ate.open')
      await expect(control).toHaveCount(1)
      await page.locator('.ate-menu .ate-item:not([disabled])', { hasText: name }).click()
      await expect.poll(() => setMembers(apiContext, setId)).toEqual([first])
      // Shut by its own button: Escape could reach the lightbox and close it.
      await setButton.click()
      await expect(control).toHaveCount(0)
      await releaseFocus(page)

      await lightbox.showNext()
      await expect.poll(() => shownPictureId(lightbox)).not.toBe(first)
      const second = await shownPictureId(lightbox)
      await page.keyboard.press('a')
      await expect
        .poll(() => setMembers(apiContext, setId))
        .toEqual([first, second].sort())
    } finally {
      const res = await apiContext.delete(`/api/v1/picture_sets/${setId}`)
      expect(res.ok()).toBe(true)
    }
  })

  test('every lightbox key types into the tag field and does nothing else', async ({
    page,
    lightbox,
    apiContext,
  }) => {
    const id = openPictureId(page)
    const before = await score(apiContext, id)
    await setScore(apiContext, id, 0)
    try {
      await lightbox.tagInput.click()
      await expect(lightbox.tagInput).toBeFocused()

      const typed = 'zsat 12345'
      for (const key of typed) await page.keyboard.press(key === ' ' ? 'Space' : key)
      await expect(lightbox.tagInput).toHaveValue(typed)

      await expect(zoomButton(page)).toHaveAttribute('aria-label', /\(fit\)/)
      await expect(shell(page)).not.toHaveClass(/chrome-hidden/)
      await expect(lightbox.pane).not.toHaveClass(/inspector--collapsed/)
      expect(openPictureId(page)).toBe(id)
      await expect.poll(() => lightbox.filledStarCount()).toBe(0)
      expect(await score(apiContext, id)).toBe(0)

      // Nothing was committed: no Enter, so no tag either.
      await lightbox.tagInput.fill('')
      await page.keyboard.press('Escape')
      await expect(lightbox.tag(typed)).toHaveCount(0)
    } finally {
      await setScore(apiContext, id, before)
    }
  })
})
