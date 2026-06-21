import { test, expect, readClientId, attachWsSniffer } from '../fixtures/test.js'

// Grid-refresh cleanup — Phase 2/4. Pins the **pill-on-own-change** defect class
// (plan §1.1, §7 "Confirmed root causes — pill-on-own-change").
//
// Premise: a change the CURRENT tab initiated must be reconciled silently in
// place — it must NEVER raise the "New pictures" or "View changed externally"
// pill. The frontend distinguishes own vs. foreign by comparing the event's
// `origin_client_id` to its own per-tab clientId (sessionStorage
// `pixlstash:clientId`). When an own request carries `X-Client-Id` but the
// backend emit site DROPS the origin, the echo arrives origin-less, the
// frontend defaults it to source:"external", and a pill appears on the user's
// own action — the bug.
//
// We simulate "the user's own action from their own tab" by driving the
// mutation through `apiContext` WITH header `X-Client-Id: <the page's clientId>`.
// The browser's WS socket receives the broadcast just as it would for a real
// in-tab action. We use the WS sniffer to confirm the echo frame actually
// arrived before asserting "no pill", so a negative result is a real bug, not a
// lost message.
//
// EXPECTED-FAIL cases are marked inline. They are real assertions that fail
// against the current (un-fixed) backend; Phase 6 flips them green by threading
// origin_client_id into the offending emit sites. Do NOT convert them to
// test.fixme — a failing assertion IS the acceptance evidence.

const CLIENT_HEADER = 'X-Client-Id'

/** Extract the picture id from a thumbnail src (.../thumbnails/<id>.webp). */
function idFromThumbSrc(src) {
  const m = /\/thumbnails\/(\d+)\./.exec(src || '')
  return m ? Number(m[1]) : null
}

/** Read a handful of visible picture ids straight off the rendered grid. */
async function visiblePictureIds(grid, count = 3) {
  const srcs = await grid.thumbnailImages.evaluateAll((imgs) =>
    imgs.map((i) => i.getAttribute('src')),
  )
  const ids = srcs.map(idFromThumbSrc).filter((id) => id != null)
  return ids.slice(0, count)
}

/** Read a picture's current score (so we can PATCH it to a guaranteed-new
 * value and be sure the backend actually broadcasts a change). */
async function currentScore(apiContext, picId) {
  const res = await apiContext.get(`/api/v1/pictures/${picId}/metadata`)
  if (!res.ok()) return null
  const meta = await res.json()
  return meta?.score ?? null
}

/** Wait until the sniffer has captured a pictures_changed frame for `picId`. */
async function waitForPicturesChangedFrame(page, wsSniffer, picId) {
  await expect
    .poll(
      () =>
        wsSniffer
          .ofType('pictures_changed')
          .some((f) => (f.picture_ids || []).includes(picId)),
      { timeout: 8000, message: `no pictures_changed frame for picture ${picId}` },
    )
    .toBe(true)
}

// The WS sniffer is attached per-test in beforeEach (before navigation) and
// shared with the test body — attaching before grid.goto() is what makes it
// reliably catch the SPA's /ws/updates socket on every test of the file.
let wsSniffer

test.describe('grid: own change must not raise a pill', () => {
  test.beforeEach(async ({ page, grid }) => {
    wsSniffer = attachWsSniffer(page)
    await grid.goto()
    await grid.waitForThumbnailLoaded()
  })

  // --- POSITIVE CONTROL: the correct-pattern emit site (SHOULD PASS now) ---
  // PATCH /pictures/{id} threads origin_client_id + picture_ids + change_kind,
  // exactly the reference pattern the buggy sites should copy (plan §7
  // "Reference (correct) pattern"). With a matching X-Client-Id the frontend
  // recognises its own echo and suppresses it: no pill. This guards the fix
  // against regressing the already-correct path.
  test('PATCH /pictures/{id} with own X-Client-Id raises no pill (control, passes now)', async ({
    page,
    apiContext,
    grid,
  }) => {
    const clientId = await readClientId(page)
    expect(clientId, 'SPA must expose a clientId').toBeTruthy()

    const [picId] = await visiblePictureIds(grid, 1)
    expect(picId, 'need a visible picture id').toBeTruthy()

    // Pick a NEW score so the backend definitely broadcasts (a no-op PATCH
    // wouldn't emit). Reversible: we restore the original afterwards.
    const original = await currentScore(apiContext, picId)
    const newScore = original === 3 ? 4 : 3
    const res = await apiContext.patch(`/api/v1/pictures/${picId}`, {
      headers: { [CLIENT_HEADER]: clientId },
      data: { score: newScore },
    })
    expect(res.ok(), await res.text()).toBeTruthy()

    await waitForPicturesChangedFrame(page, wsSniffer, picId)
    // The echo carried our origin → own-echo → suppressed. No pill.
    await grid.expectNoPill()

    // Restore the fixture (still own-origin, still no pill).
    await apiContext.patch(`/api/v1/pictures/${picId}`, {
      headers: { [CLIENT_HEADER]: clientId },
      data: { score: original ?? 0 },
    })
  })

  // --- CONFIRMED-BUGGY EMIT SITE: tag_predictions/delete -------------------
  // POST /pictures/{id}/tag_predictions/delete emits CHANGED_PICTURES with
  // picture_ids but NO origin_client_id, even when the request carries
  // X-Client-Id (plan §7 cause #6, tag_predictions.py:216). The origin-less
  // CHANGED_PICTURES → frontend source:"external" → "View changed externally"
  // pill on the user's OWN action.
  test('tag_predictions/delete with own X-Client-Id raises no pill (regression: #499 origin threading)', async ({
    page,
    apiContext,
    grid,
  }) => {
    const clientId = await readClientId(page)
    expect(clientId).toBeTruthy()

    const [picId] = await visiblePictureIds(grid, 1)
    expect(picId).toBeTruthy()

    const res = await apiContext.post(
      `/api/v1/pictures/${picId}/tag_predictions/delete`,
      { headers: { [CLIENT_HEADER]: clientId } },
    )
    expect(res.ok(), await res.text()).toBeTruthy()

    // Confirm the echo frame really arrived, so a pill failure is the bug and
    // not a dropped message.
    await waitForPicturesChangedFrame(page, wsSniffer, picId)
    // The arrived frame should carry our origin but does NOT (the bug):
    const frame = wsSniffer
      .ofType('pictures_changed')
      .find((f) => (f.picture_ids || []).includes(picId))
    expect(
      frame.origin_client_id,
      'REGRESSION (#499): backend dropped origin_client_id on this own change',
    ).toBe(clientId)

    // Consequence assertion: own change must not pill.
    await grid.expectNoPill()
  })

  // --- CONFIRMED-BUGGY EMIT SITE: tag_predictions/{tag}/reject -------------
  // Same root cause, different handler: reject_tag_prediction also emits a
  // bare-origin CHANGED_PICTURES (tag_predictions.py:193).
  test('tag_predictions/{tag}/reject with own X-Client-Id raises no pill (regression: #499 origin threading)', async ({
    page,
    apiContext,
    grid,
  }) => {
    const clientId = await readClientId(page)
    expect(clientId).toBeTruthy()

    const [picId] = await visiblePictureIds(grid, 1)
    expect(picId).toBeTruthy()

    // reject is idempotent and harmless even if the tag doesn't exist; it still
    // fires the CHANGED_PICTURES echo for this picture.
    const res = await apiContext.post(
      `/api/v1/pictures/${picId}/tag_predictions/zz-e2e-nonexistent-tag/reject`,
      { headers: { [CLIENT_HEADER]: clientId } },
    )
    expect(res.ok(), await res.text()).toBeTruthy()

    await waitForPicturesChangedFrame(page, wsSniffer, picId)
    const frame = wsSniffer
      .ofType('pictures_changed')
      .find((f) => (f.picture_ids || []).includes(picId))
    expect(
      frame.origin_client_id,
      'REGRESSION (#499): backend dropped origin_client_id on this own change',
    ).toBe(clientId)

    await grid.expectNoPill()
  })

  // --- CONFIRMED-BUGGY EMIT SITE: picture_sets membership ------------------
  // POST /picture_sets/{id}/members emits a bare CHANGED_PICTURES (no ids, no
  // origin) on every membership add (plan §7 cause #3, picture_sets.py:1546).
  // With empty ids the frontend can't target it, so the own echo falls to a
  // full grid RELOAD rather than a pill — a different but related own-change
  // defect (the grid churns on the user's own set edit). We assert no pill AND
  // that the echo arrived origin-less, pinning the dropped-origin cause.
  test('picture_sets/{id}/members add with own X-Client-Id echoes origin (regression: #499 origin threading)', async ({
    page,
    apiContext,
    grid,
  }) => {
    const clientId = await readClientId(page)
    expect(clientId).toBeTruthy()

    const setsRes = await apiContext.get('/api/v1/picture_sets')
    expect(setsRes.ok(), await setsRes.text()).toBeTruthy()
    const sets = await setsRes.json()
    test.skip(!sets.length, 'fixture has no picture sets')
    const setId = sets[0].id

    // Add only NON-members: bulk_add broadcasts only when it actually inserts a
    // row, and the shared per-run backend may already have some of these
    // pictures in this set (added by an earlier spec), so we must compute the
    // difference against the set's current members rather than assume.
    const membersRes = await apiContext.get(
      `/api/v1/picture_sets/${setId}/members`,
    )
    expect(membersRes.ok(), await membersRes.text()).toBeTruthy()
    const members = new Set((await membersRes.json()).picture_ids || [])

    const candidates = await visiblePictureIds(grid, 30)
    const ids = candidates.filter((id) => !members.has(id)).slice(0, 5)
    test.skip(
      !ids.length,
      'all visible pictures already in set[0]; no add to judge',
    )

    const res = await apiContext.post(
      `/api/v1/picture_sets/${setId}/members`,
      { headers: { [CLIENT_HEADER]: clientId }, data: { picture_ids: ids } },
    )
    expect(res.ok(), await res.text()).toBeTruthy()
    const added = (await res.json()).added
    // Now the add is guaranteed to touch only non-members, so it must broadcast.
    expect(added, 'membership add must have added at least one picture').toBeGreaterThan(0)

    // The membership add broadcasts a bare pictures_changed (no ids). Wait for
    // ANY pictures_changed frame to land, then inspect its origin.
    await expect
      .poll(() => wsSniffer.countOfType('pictures_changed'), {
        timeout: 8000,
        message: 'no pictures_changed frame from membership add',
      })
      .toBeGreaterThan(0)

    const frame = wsSniffer.ofType('pictures_changed').at(-1)
    expect(
      frame.origin_client_id,
      'REGRESSION (#499): picture_sets membership add dropped origin_client_id',
    ).toBe(clientId)
  })
})
