import { test, expect } from '../fixtures/test.js'

// Grid-refresh cleanup — Phase 2/4. The overlay-deferral contract
// (useGridRealtimeSync.js deferWhileOverlayOpen / ImageGrid.closeOverlay):
// while the lightbox overlay is open the grid must NOT raise a pill or reshuffle
// under the frozen filmstrip. Instead the change is flagged as a deferred
// in-place reconcile that runs when the overlay closes.
//
// This direction SHOULD PASS now (the deferral already exists). It's the
// regression net so a Phase 6 coalescing/origin change doesn't break the
// overlay-open quietness or the close-time catch-up.
//
// Signal: the grid's full refetch is GET /pictures/stream. We assert no stream
// refetch (and no pill) while the overlay is open after an external event, and
// a stream refetch after the overlay closes (the deferred reconcile).

const INJECT = '/api/v1/test-hooks/ws-event'

function idFromThumbSrc(src) {
  const m = /\/thumbnails\/(\d+)\./.exec(src || '')
  return m ? Number(m[1]) : null
}

async function visiblePictureIds(grid, count = 3) {
  const srcs = await grid.thumbnailImages.evaluateAll((imgs) =>
    imgs.map((i) => i.getAttribute('src')),
  )
  return srcs.map(idFromThumbSrc).filter((id) => id != null).slice(0, count)
}

test.describe('grid: external change while overlay open is deferred', () => {
  test('external update during open overlay raises no pill, reconciles on close', async ({
    page,
    apiContext,
    grid,
    overlay,
  }) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    const ids = await visiblePictureIds(grid, 2)
    expect(ids.length).toBeGreaterThan(0)

    // Open the lightbox on the first card.
    await overlay.openFromGrid()
    await expect(overlay.root).toBeVisible()

    // Start counting grid stream refetches from AFTER the overlay is open, so
    // the initial-load stream isn't counted.
    let streamRequests = 0
    page.on('request', (req) => {
      if (/\/pictures\/stream\b/.test(req.url())) streamRequests += 1
    })

    // Inject an external, view-affecting `updated` event. Outside an overlay
    // this would raise the "View changed externally" pill; with the overlay
    // open it must be deferred instead.
    const res = await apiContext.post(INJECT, {
      data: {
        event_type: 'CHANGED_PICTURES',
        picture_ids: ids,
        source: 'external',
        change_kind: 'updated',
      },
    })
    expect(res.ok(), await res.text()).toBeTruthy()

    // While the overlay is open: no pill, no grid reshuffle/refetch.
    await page.waitForTimeout(1500)
    await expect(grid.sortChangedPill()).toHaveCount(0)
    await expect(grid.pendingImportsPill()).toHaveCount(0)
    expect(
      streamRequests,
      'grid must not refetch while the overlay is open',
    ).toBe(0)

    // Close the overlay → the deferred reconcile runs (a full grid refetch).
    await overlay.close()
    await expect
      .poll(() => streamRequests, {
        timeout: 8000,
        message: 'deferred reconcile (grid stream refetch) did not fire on close',
      })
      .toBeGreaterThan(0)

    // And the deferral must not have left a stale pill behind.
    await expect(grid.sortChangedPill()).toHaveCount(0)
  })
})
