import { test, expect, readClientId, attachWsSniffer } from '../fixtures/test.js'

// Grid-refresh cleanup — Phase 2/4. Drives the guarded e2e-only injection
// endpoint POST /api/v1/test-hooks/ws-event (pixlstash/routes/test_hooks.py)
// directly, so events fire deterministically without real background-worker
// timing. The endpoint calls vault.notify with a controlled payload, so the
// broadcast is byte-for-byte identical to a real emit site.
//
// Covers three things the plan calls out (§4 Phase 2, §7):
//   (i)   own-origin echo (origin_client_id == this tab) → suppressed, no pill.
//   (ii)  external `added` → "New pictures" pill (positive direction).
//   (iii) a 100x external flood → the grid must not refresh an unbounded number
//         of times. This pins the continuous-refresh root cause (plan §7
//         "no frontend coalescing of incoming WS picture events"). It is
//         EXPECTED to FAIL now: each foreign updated event fires its own
//         per-card /metadata fetch with no coalescing.

const INJECT = '/api/v1/test-hooks/ws-event'

/** Extract the picture id from a thumbnail src (.../thumbnails/<id>.webp). */
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

// Attached per-test before navigation (see grid-own-change-no-pill for why).
let wsSniffer

test.describe('grid: injected WS events', () => {
  test.beforeEach(async ({ page, grid }) => {
    wsSniffer = attachWsSniffer(page)
    await grid.goto()
    await grid.waitForThumbnailLoaded()
  })

  // (i) An injected event stamped with THIS tab's clientId is the tab's own
  // echo. The frontend must suppress it (no pill), even though it's an `added`
  // event that would otherwise raise the "New pictures" pill.
  test('own-origin injected event is suppressed (no pill)', async ({
    page,
    apiContext,
    grid,
  }) => {
    const clientId = await readClientId(page)
    expect(clientId).toBeTruthy()
    const ids = await visiblePictureIds(grid, 2)
    expect(ids.length).toBeGreaterThan(0)

    const res = await apiContext.post(INJECT, {
      data: {
        event_type: 'CHANGED_PICTURES',
        picture_ids: ids,
        origin_client_id: clientId,
        change_kind: 'added',
      },
    })
    expect(res.ok(), await res.text()).toBeTruthy()

    // Own echo → suppressed. Give the frame time to be processed, then assert
    // neither pill appeared.
    await grid.expectNoPill(3000)
  })

  // (ii) An external `added` event (no origin, source:"external") raises the
  // "New pictures" pill. Positive direction; guards against over-suppression.
  test('external added injected event raises the "New pictures" pill', async ({
    apiContext,
    grid,
  }) => {
    const ids = await visiblePictureIds(grid, 2)
    expect(ids.length).toBeGreaterThan(0)

    const res = await apiContext.post(INJECT, {
      data: {
        event_type: 'CHANGED_PICTURES',
        picture_ids: ids,
        source: 'external',
        change_kind: 'added',
      },
    })
    expect(res.ok(), await res.text()).toBeTruthy()

    await expect(grid.pendingImportsPill()).toBeVisible()
    // Clear it so it doesn't leak into a later spec.
    await grid.pendingImportsPill().click()
  })

  // (iii) FLOOD / COALESCING. Inject 100 external owner-UI `updated` events for
  // a single visible id. Each one drives a per-card /metadata refetch
  // (applyTargetedUpdate → refreshGridImage → fetchImageInfo) with NO
  // coalescing. We count the /metadata requests the grid issues. A healthy grid
  // would coalesce a burst into a small bounded number of refetches; the
  // current grid fires roughly one per event.
  //
  // Regression guard for #500: frontend coalescing of incoming WS picture
  // events (plan §7 continuous-refresh cause #3). Before the fix this issued
  // ~100 /metadata refetches; the 200ms coalescing window now collapses the
  // flood to a single batched refresh.
  test('100x external flood does not trigger an unbounded number of grid refreshes (regression: #500 frontend WS coalescing)', async ({
    page,
    apiContext,
    grid,
  }) => {
    const [picId] = await visiblePictureIds(grid, 1)
    expect(picId).toBeTruthy()

    const REPEAT = 100
    // A generous upper bound for a coalesced grid: even one refetch per ~120ms
    // over the time it takes to deliver 100 frames is well under this. The
    // un-fixed grid blows past it (≈ one /metadata fetch per event).
    const MAX_REFRESHES = 15

    // Count per-card metadata refetches — the direct measure of grid refreshes.
    let metadataRequests = 0
    page.on('request', (req) => {
      if (/\/pictures\/\d+\/metadata/.test(req.url())) metadataRequests += 1
    })

    wsSniffer.clear()
    const res = await apiContext.post(INJECT, {
      data: {
        event_type: 'CHANGED_PICTURES',
        picture_ids: [picId],
        source: 'ui', // owner-UI from a different (here: absent) origin → foreign-UI path
        change_kind: 'updated',
        repeat: REPEAT,
      },
    })
    expect(res.ok(), await res.text()).toBeTruthy()
    expect((await res.json()).emitted).toBe(REPEAT)

    // Wait until all 100 frames have been delivered to the browser, so the
    // count reflects the grid's full reaction to the flood.
    await expect
      .poll(() => wsSniffer.countOfType('pictures_changed'), {
        timeout: 15000,
        message: 'flood frames did not all arrive',
      })
      .toBeGreaterThanOrEqual(REPEAT)

    // Let any queued refetches settle.
    await page.waitForTimeout(1500)

    expect(
      metadataRequests,
      `REGRESSION (#500): grid issued ${metadataRequests} /metadata refetches for a ` +
        `${REPEAT}x flood; coalescing should keep it <= ${MAX_REFRESHES}.`,
    ).toBeLessThanOrEqual(MAX_REFRESHES)
  })
})
