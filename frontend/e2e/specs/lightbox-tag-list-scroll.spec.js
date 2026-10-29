import { test, expect } from '../fixtures/test.js'

// The lightbox sidebar is a fixed-height flex column (.lightbox-sidebar is
// `overflow: hidden`). Info is the grid rail's one-picture view, a column
// that scrolls as a whole, so a heavily tagged picture scrolls Description
// and Properties into reach rather than pushing them off the bottom, where
// the extra chips would be clipped with no way to reach them.

/** Extract the picture id from a thumbnail src (.../thumbnails/<id>.webp). */
function idFromThumbSrc(src) {
  const m = /\/thumbnails\/(\d+)\./.exec(src || '')
  return m ? Number(m[1]) : null
}

/** Box + scroll geometry of a single element, read in one page evaluation. */
function geometry(locator) {
  return locator.evaluate((el) => {
    const style = getComputedStyle(el)
    const rect = el.getBoundingClientRect()
    return {
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      bottom: rect.bottom,
      overflowY: style.overflowY,
      maxHeight: style.maxHeight,
      display: style.display,
      flexDirection: style.flexDirection,
    }
  })
}

test.describe('lightbox Info stays scrollable', () => {
  test('a long tag list scrolls the Info column inside the sidebar', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    // A short viewport is the "space is limited" case.
    await page.setViewportSize({ width: 1280, height: 600 })
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    const src = await grid.thumbnailImages.first().getAttribute('src')
    const pictureId = idFromThumbSrc(src)
    expect(pictureId, 'a grid thumbnail must expose its picture id').toBeTruthy()

    // Enough chips to overflow any sane sidebar height. Added through the API
    // rather than the UI so the setup is fast and does not depend on the
    // completions; removed again in `finally` so the shared fixture is
    // restored even if an assertion fails.
    const added = []
    try {
      for (let i = 0; i < 40; i++) {
        const res = await apiContext.post(`/api/v1/pictures/${pictureId}/tags`, {
          data: { tag: `e2e-scroll-probe-${i}` },
        })
        expect(res.ok(), `adding probe tag ${i} must succeed`).toBeTruthy()
      }
      const tagsRes = await apiContext.get(`/api/v1/pictures/${pictureId}/tags`)
      const tagsPayload = await tagsRes.json()
      for (const row of tagsPayload?.tags ?? tagsPayload ?? []) {
        if (String(row.tag ?? '').startsWith('e2e-scroll-probe-')) added.push(row.id)
      }
      expect(added.length, 'probe tags must come back with ids').toBe(40)

      await page.goto(`/?overlay=${pictureId}`)
      await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
      // The pane is the right rail, shut unless the reader opened it.
      await lightbox.openRail()
      await expect(lightbox.tags.first()).toBeVisible()

      const column = page.locator('.lightbox-sidebar-group')
      const sidebar = page.locator('.lightbox-sidebar')

      const columnBox = await geometry(column)
      expect(columnBox.overflowY).toBe('auto')
      // 40 chips cannot fit in a 600px-tall sidebar, so the column must scroll.
      expect(columnBox.scrollHeight).toBeGreaterThan(columnBox.clientHeight)

      // ...and it must scroll *within* the sidebar rather than overflowing it.
      const sidebarBox = await geometry(sidebar)
      expect(columnBox.bottom).toBeLessThanOrEqual(sidebarBox.bottom + 1)

      // The scrollbar has to actually move, and bring Properties into view.
      await column.evaluate((el) => {
        el.scrollTop = el.scrollHeight
      })
      expect(await column.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
      await expect(
        page.locator('.lightbox .rail-picture__section', { hasText: 'Properties' }),
      ).toBeInViewport()
    } finally {
      for (const tagId of added) {
        await apiContext.delete(`/api/v1/pictures/${pictureId}/tags/${tagId}`)
      }
    }
  })
})
