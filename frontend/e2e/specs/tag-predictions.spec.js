import { test, expect } from '../fixtures/test.js'

// Release plan §14 — Tag predictions. The current UI surfaces predictions as
// the "Rejected Tags" chips in the overlay side panel: deleting an applied tag
// rejects its prediction, dropping the label down to a prediction chip with a
// confidence badge (only predictions the tagger actually scored ≥ 0.3 render —
// a synthetic manual reject gets confidence 0.0 and stays hidden by design),
// and the chip's ✓ button confirms the prediction back into the applied list.
//
// The fixture vault carries real tagger predictions, so the spec discovers a
// picture whose APPLIED tag has a live prediction ≥ 0.35 via the API, then
// drives the full round trip in the overlay:
//
//   delete tag → drops to prediction chip → Confirm → returns to applied tags.
//
// The round trip restores the tag, so the shared fixture ends as it began.
// Prediction *generation* after a fresh import needs background workers, which
// the e2e harness disables — that path stays in the manual release plan.

/** Extract the picture id from a thumbnail src (.../thumbnails/<id>.webp). */
function idFromThumbSrc(src) {
  const m = /\/thumbnails\/(\d+)\./.exec(src || '')
  return m ? Number(m[1]) : null
}

/**
 * Find a picture (among the visible grid thumbnails) with an applied tag whose
 * prediction row has a real confidence ≥ 0.35 — high enough that a reject
 * keeps the chip above the UI's 0.3 near-miss render threshold.
 */
async function findPredictedAppliedTag(grid, apiContext) {
  const srcs = await grid.thumbnailImages.evaluateAll((imgs) =>
    imgs.map((i) => i.getAttribute('src')),
  )
  const ids = srcs.map(idFromThumbSrc).filter((id) => id != null)
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
    const match = strong.find((p) =>
      applied.includes(p.tag.trim().toLowerCase()),
    )
    if (match) return { pictureId: id, tag: match.tag }
  }
  return null
}

test.describe('tag predictions', () => {
  test('a deleted tag drops to a prediction chip and Confirm restores it (§14)', async ({
    page,
    grid,
    overlay,
    apiContext,
  }) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    const found = await findPredictedAppliedTag(grid, apiContext)
    expect(
      found,
      'fixture must contain a visible picture with an applied tag predicted ≥ 0.35',
    ).toBeTruthy()
    const { pictureId, tag } = found

    // Deep-link the overlay to the discovered picture (?overlay=<id>).
    await page.goto(`/?overlay=${pictureId}`)
    await expect(overlay.root).toBeVisible({ timeout: 15_000 })
    await expect(overlay.tag(tag)).toBeVisible()

    const predictionChip = page
      .locator('.tag-drop-zone--predictions .overlay-tag--prediction')
      .filter({ hasText: tag })
      .first()

    // Delete the applied tag: it must drop down to the predictions zone with
    // its confidence badge, not vanish (plan: "Deleting a tag causes it to
    // drop down to prediction IF it had a > 0.0 prediction").
    await overlay.removeTag(tag)
    await expect(overlay.tag(tag)).toBeHidden()
    await expect(predictionChip).toBeVisible()
    await expect(predictionChip.locator('.tag-pred-confidence')).toHaveText(/\d+%/)

    // Confirm the prediction: the tag returns to the applied list and the chip
    // leaves the predictions zone (plan: "Accept moves it to the confirmed tag
    // list and it disappears from predictions"). This restores the fixture.
    await predictionChip.hover()
    await predictionChip.locator('.tag-pred-btn--confirm').click()
    await expect(overlay.tag(tag)).toBeVisible()
    await expect(predictionChip).toBeHidden()

    // Backend truth: the tag row really is back.
    const tagsRes = await apiContext.get(`/api/v1/pictures/${pictureId}/tags`)
    expect(tagsRes.ok()).toBeTruthy()
    const tagsPayload = await tagsRes.json()
    const applied = (tagsPayload?.tags ?? []).map((t) =>
      String(t.tag ?? t).trim().toLowerCase(),
    )
    expect(applied).toContain(tag.trim().toLowerCase())
  })
})
