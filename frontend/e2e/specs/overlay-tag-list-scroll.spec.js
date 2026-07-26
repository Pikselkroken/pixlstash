import { test, expect } from '../fixtures/test.js'

// The overlay sidebar is a fixed-height flex column (.overlay-sidebar is
// `overflow: hidden`), so both tag lists have to bound themselves and scroll —
// otherwise a heavily tagged picture pushes Metadata off the bottom and the
// extra chips are simply clipped with no way to reach them.
//
// The regression this guards is subtle: OverlayTagsPanel has MULTIPLE ROOT
// NODES, so Vue never stamps ImageOverlay's scope id onto them. The section
// layout (`display: flex` + a `min-height` floor) therefore has to live in
// OverlayTagsPanel's own scoped block; written in ImageOverlay it compiles
// fine, matches nothing, and `.tag-list`'s `overflow-y: auto` silently never
// resolves into a scrollbar because its parent is an auto-height block.

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

test.describe('overlay sidebar tag lists stay scrollable', () => {
  test('the applied-tag list scrolls instead of growing past the sidebar', async ({
    page,
    grid,
    overlay,
    apiContext,
  }) => {
    // A short viewport is the "space is limited" case the fix targets.
    await page.setViewportSize({ width: 1280, height: 600 })
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    const src = await grid.thumbnailImages.first().getAttribute('src')
    const pictureId = idFromThumbSrc(src)
    expect(pictureId, 'a grid thumbnail must expose its picture id').toBeTruthy()

    // Enough chips to overflow any sane sidebar height. Added through the API
    // rather than the UI so the setup is fast and does not depend on the
    // autocomplete; removed again in `finally` so the shared fixture is
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
      await expect(overlay.root).toBeVisible({ timeout: 15_000 })
      await expect(overlay.tags.first()).toBeVisible()

      const section = page.locator('.sidebar-section--tags')
      const list = page.locator('.tag-list')
      const sidebar = page.locator('.overlay-sidebar')

      // The scope-id trap: if these rules stop applying, the section is a plain
      // block, the list has no definite height, and nothing below can hold.
      const sectionBox = await geometry(section)
      expect(sectionBox.display).toBe('flex')
      expect(sectionBox.flexDirection).toBe('column')

      const listBox = await geometry(list)
      expect(listBox.overflowY).toBe('auto')
      // 40 chips cannot fit in a 600px-tall sidebar, so the list must scroll.
      expect(listBox.scrollHeight).toBeGreaterThan(listBox.clientHeight)

      // ...and it must scroll *within* the sidebar rather than overflowing it.
      const sidebarBox = await geometry(sidebar)
      expect(listBox.bottom).toBeLessThanOrEqual(sidebarBox.bottom + 1)

      // The scrollbar has to actually move, not just exist.
      await list.evaluate((el) => {
        el.scrollTop = el.scrollHeight
      })
      expect(await list.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
    } finally {
      for (const tagId of added) {
        await apiContext.delete(`/api/v1/pictures/${pictureId}/tags/${tagId}`)
      }
    }
  })

  test('the rejected-tag list is bounded and scrollable', async ({
    page,
    grid,
    overlay,
    apiContext,
  }) => {
    await page.setViewportSize({ width: 1280, height: 600 })
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    // Rejected Tags only renders when a prediction sits above the 0.3 near-miss
    // threshold, so borrow tag-predictions.spec's approach: find an applied tag
    // that the tagger actually scored, then delete it to drop it into the
    // rejected list. The tag is confirmed back afterwards to leave the shared
    // fixture as it was found.
    const srcs = await grid.thumbnailImages.evaluateAll((imgs) =>
      imgs.map((i) => i.getAttribute('src')),
    )
    const ids = srcs.map(idFromThumbSrc).filter((id) => id != null)

    let found = null
    for (const id of ids) {
      const predsRes = await apiContext.get(`/api/v1/pictures/${id}/tag_predictions`)
      if (!predsRes.ok()) continue
      const payload = await predsRes.json()
      const preds = Array.isArray(payload) ? payload : (payload?.tag_predictions ?? [])
      const strong = preds.filter(
        (p) => (p.confidence ?? 0) >= 0.35 && p.status !== 'REJECTED',
      )
      if (!strong.length) continue
      const tagsRes = await apiContext.get(`/api/v1/pictures/${id}/tags`)
      if (!tagsRes.ok()) continue
      const tagsPayload = await tagsRes.json()
      const applied = (tagsPayload?.tags ?? tagsPayload ?? []).map((t) =>
        String(t.tag ?? t).trim().toLowerCase(),
      )
      const match = strong.find((p) => applied.includes(p.tag.trim().toLowerCase()))
      if (match) {
        found = { pictureId: id, tag: match.tag }
        break
      }
    }
    expect(
      found,
      'fixture must contain a visible picture with an applied tag predicted ≥ 0.35',
    ).toBeTruthy()

    await page.goto(`/?overlay=${found.pictureId}`)
    await expect(overlay.root).toBeVisible({ timeout: 15_000 })
    await expect(overlay.tag(found.tag)).toBeVisible()

    await overlay.removeTag(found.tag)

    const rejected = page.locator('.tag-drop-zone--predictions')
    await expect(rejected).toBeVisible()

    const box = await geometry(rejected)
    // Bounded: without a cap this list grows without limit and shoves the
    // Metadata section out of the sidebar.
    expect(box.maxHeight).not.toBe('none')
    expect(box.overflowY).toBe('auto')
    // Whatever it holds, it never exceeds its own cap.
    expect(box.clientHeight).toBeLessThanOrEqual(parseFloat(box.maxHeight))

    const sidebarBox = await geometry(page.locator('.overlay-sidebar'))
    expect(box.bottom).toBeLessThanOrEqual(sidebarBox.bottom + 1)

    // The section must be tall enough to actually hold its own chips. Flex item
    // *boxes* never overlap, so a bounds check between sections cannot catch
    // this: the failure mode is a section squeezed below its content height,
    // which keeps painting that content straight over the Metadata panel below.
    const sectionRect = await page
      .locator('.sidebar-section--rejected-tags')
      .boundingBox()
    const zoneRect = await rejected.boundingBox()
    expect(zoneRect.y + zoneRect.height).toBeLessThanOrEqual(
      sectionRect.y + sectionRect.height + 1,
    )

    // Restore the fixture: confirm the prediction back into the applied list.
    await rejected
      .locator('.overlay-tag', { hasText: found.tag })
      .first()
      .locator('.tag-pred-btn--confirm')
      .click()
    await expect(overlay.tag(found.tag)).toBeVisible()
  })
})
