import { test, expect } from '../fixtures/test.js'

// Grid-refresh cleanup — Phase 2/4. The POSITIVE direction of the
// pill-on-own-change defect: an EXTERNAL change (one this tab did NOT initiate)
// MUST raise the correct pill. This is the over-blocking guard: when Phase 6
// threads origin into the buggy emit sites, the fix must NOT also suppress
// genuinely-foreign events (over-blocking is its own regression — see the repo's
// "tests assert both directions" rule).
//
// We simulate a foreign change by driving the mutation through `apiContext`
// WITHOUT an X-Client-Id header. The backend then broadcasts an origin-less
// event, which the frontend correctly classifies as source:"external" and
// surfaces as a pill. These SHOULD PASS now and must keep passing after Phase 6.
//
// Note: an external "updated" event that affects the current sort/filter raises
// the "View changed externally" pill; an external "added" event raises the "New
// pictures" pill. The added→pill direction is exercised deterministically in
// grid-injection.spec.js via the test-hooks endpoint (no apiContext mutation
// cleanly produces an `added` broadcast against the shared fixture).

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

test.describe('grid: external change raises the correct pill', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()
  })

  // PATCH /pictures/{id} WITHOUT X-Client-Id → origin-less CHANGED_PICTURES,
  // change_kind:"updated" → external-updated-sort-affecting → sort-changed pill.
  test('PATCH /pictures/{id} without X-Client-Id raises the "View changed externally" pill', async ({
    apiContext,
    grid,
  }) => {
    const [picId] = await visiblePictureIds(grid, 1)
    expect(picId, 'need a visible picture id').toBeTruthy()

    // Pick a NEW score so the backend definitely broadcasts a change.
    const metaRes = await apiContext.get(`/api/v1/pictures/${picId}/metadata`)
    const original = metaRes.ok() ? ((await metaRes.json())?.score ?? null) : null
    const newScore = original === 4 ? 3 : 4
    const res = await apiContext.patch(`/api/v1/pictures/${picId}`, {
      data: { score: newScore },
    })
    expect(res.ok(), await res.text()).toBeTruthy()

    // Foreign updated event → the grid must NOT reshuffle under the user; it
    // raises the click-to-refresh pill instead.
    await expect(grid.sortChangedPill()).toBeVisible()
    // The "new pictures" pill is for adds; an update must not raise it.
    await expect(grid.pendingImportsPill()).toHaveCount(0)

    // Clear the pill (clicking refreshes) and restore the fixture.
    await grid.sortChangedPill().click()
    await apiContext.patch(`/api/v1/pictures/${picId}`, {
      data: { score: original ?? 0 },
    })
  })

  // tag_predictions/delete WITHOUT X-Client-Id → origin-less CHANGED_PICTURES
  // carrying the picture id → external-updated-sort-affecting → sort-changed
  // pill. (This is the same emit site the own-change spec marks EXPECTED FAIL;
  // for a foreign caller the pill is the CORRECT behaviour and passes now.)
  test('tag_predictions/delete without X-Client-Id raises the "View changed externally" pill', async ({
    apiContext,
    grid,
  }) => {
    const [picId] = await visiblePictureIds(grid, 1)
    expect(picId).toBeTruthy()

    const res = await apiContext.post(
      `/api/v1/pictures/${picId}/tag_predictions/delete`,
    )
    expect(res.ok(), await res.text()).toBeTruthy()

    await expect(grid.sortChangedPill()).toBeVisible()
    await grid.sortChangedPill().click()
  })
})
