import { test, expect } from '../fixtures/test.js'
import { ReviewSessions } from '../pages/ReviewSessions.js'

// Review Sessions — the session loop against the real backend. Each test uses a
// DISTINCT tag: the backend enforces one open review per tag, and the suite runs
// single-worker against one shared, mutable backend, so two tests creating the
// same tag would 409. The fixture vault's real CLIP embeddings + the base-rate-
// relative scan thresholds produce these whole-vault suspect counts (re-probed
// 2026-10-30 by creating a review per tag on the e2e backend): black hair 2
// (remove), smile 2 (remove), close-up 4 (2 remove + 2 add), arm hair 3
// (1 remove + 2 add), closed eyes / woman 1 (remove). The queue orders
// remove-direction cards first. 'shirt' now yields 0 suspects — the base-rate-
// relative threshold work (6c79e6de / 02fe21c2) moved it below eligibility — so
// it can no longer back a session test; do NOT reintroduce it here. Single-
// suspect tags ('beard', 'grin') cannot back a test that decides and then keeps
// working: the first decision empties the queue and the decision bar goes with
// it. Assertions are relative where counts could shift; the binary, Undo and
// keyboard tests need a tag with >=2 suspects whose first card is a remove.

// Read a tally span's integer (e.g. "✗ 2" -> 2). Missing span -> 0.
async function tallyNum(locator) {
  if ((await locator.count()) === 0) return 0
  const t = (await locator.first().innerText()).replace(/[^\d]/g, '')
  return t ? parseInt(t, 10) : 0
}

// Find the open review id for a tag via the API (createReview goes through the
// UI, which does not hand the id back to the test).
async function openReviewId(apiContext, tag) {
  const res = await apiContext.get('/api/v1/reviews?status=OPEN')
  const list = await res.json()
  const match = list.find((r) => r.tag === tag)
  return match ? match.id : null
}

// The review's server-side receipt. The tally moves optimistically, before the
// decision's write lands, so a test reading this after a tally change polls it.
async function receiptOf(apiContext, rid) {
  return (await (await apiContext.get(`/api/v1/reviews/${rid}`)).json()).receipt
}

// BUG-RS-1 (RESOLVED): the session card used to never render in a production
// browser build — ReviewSessionView crashed with `TypeError: Cannot read
// properties of undefined (reading 'el')` in Vue's patchBlockChildren the moment
// the suggestions queue loaded, leaving the session stuck on "Loading…". Root
// cause: the card's `v-else` branch carried an explicit `:key="current.id"` that
// collided with the compiler's numeric auto-key (1) for the sibling "Loading"
// v-if branch when the first suggestion's id was 1. In a production build (no
// DEV_ROOT_FRAGMENT wrapping) Vue then block-patched the empty Loading <div> into
// the card <div>, desyncing dynamicChildren. Fixed by namespacing the key
// (`card-${current.id}`) in ReviewSessionView.vue.
//
// The first two specs (create + binary decide) are the authoritative BUG-RS-1
// regression guard and pass against the production build.
test.describe('review sessions — session loop', () => {
  test.beforeEach(async ({ grid, reviews, apiContext }) => {
    // Isolation: fully reset any leftover OPEN review before each attempt so a
    // prior test — or a failed FIRST attempt of this same test — cannot poison
    // the next run. Two things must be undone, in order, per open review:
    //   1. bulk-reopen its DECIDED rows (review-scoped, ids=[] = all of them;
    //      SKIPPED rows are left as-is). scan_tag runs with
    //      include_reviewed=False, so any ACCEPTED/DISMISSED suspect would
    //      otherwise be suppressed on the next create — starving a retry of
    //      suspects (the mutating 'black hair' test needs both of its 2 removes,
    //      so one leftover decision would drop the retry to a 1- or 0-suspect
    //      scan and it would die at binBanner / the empty done-state instead of
    //      recovering). Reopening flips them back to PENDING so the next scan
    //      re-adopts them. Mirrors the store's own undoChangesAndAbort flow.
    //   2. abort the review, freeing the tag's one-open-review slot so
    //      createReview() shows the Create button (.rs-dialog-btn--go) instead
    //      of jumping into the still-open session.
    const openReviews = await (
      await apiContext.get('/api/v1/reviews?status=OPEN')
    ).json()
    for (const r of openReviews) {
      await apiContext.post('/api/v1/tag_suggestions/bulk-reopen', {
        data: { review_id: r.id },
      })
      await apiContext.post(`/api/v1/reviews/${r.id}/abort`)
    }
    await grid.goto()
    await ReviewSessions.ensureHealthBuilt(apiContext)
    await reviews.open()
    await expect(reviews.boardRows.first()).toBeVisible()
  })

  test('creating a review opens a session with a scan receipt and progress', async ({ reviews, apiContext }) => {
    await reviews.createReview('man')
    await expect(reviews.toolbarTitle).toContainText('man')
    // The cover-sheet receipt reports the scan.
    await expect(reviews.sessionReceipt).toContainText('Scanned')
    await expect(reviews.sessionReceipt).toContainText('suspects')
    // The sidebar nests this session under "Tags", lit, with a
    // done/found progress hint.
    const row = reviews.sidebarSessions.filter({ hasText: 'man' }).first()
    await expect(row).toHaveAttribute('aria-current', 'page')
    await expect(row.locator('.sidebar-review-session-count')).toContainText('/')
    // A decision card is presented; Undo starts disabled (empty undo stack).
    await expect(reviews.card).toBeVisible()
    await expect(reviews.undoButton).toBeDisabled()
    // Backend agrees a review exists.
    expect(await openReviewId(apiContext, 'man')).not.toBeNull()
  })

  test('binary Yes/No map to keep/remove and the tally + backend receipt track it', async ({ reviews, apiContext }) => {
    await reviews.createReview('black hair')
    const rid = await openReviewId(apiContext, 'black hair')
    expect(rid).not.toBeNull()

    // "black hair" has 2 pure remove-direction suspects (no add-direction ones),
    // and the queue orders remove-direction cards first, so the first two cards
    // are both removes — enough to exercise No→remove then Yes→keep. The banner
    // renders for every suspect card and frames removing the tag.
    await expect(reviews.binBanner.first()).toBeVisible()

    // No on a remove card = accept = remove the tag -> "removed" tally + focus advances.
    const beforeRemoved = await tallyNum(reviews.tallyRemoved)
    await reviews.noButton.click()
    await expect.poll(async () => tallyNum(reviews.tallyRemoved)).toBe(beforeRemoved + 1)
    // The re-keyed card receives focus for the next decision.
    await expect(reviews.card).toBeFocused()
    // Undo is now enabled.
    await expect(reviews.undoButton).toBeEnabled()

    // Yes on a remove card = dismiss = keep the tag -> "kept" tally.
    const beforeKept = await tallyNum(reviews.tallyKept)
    await reviews.yesButton.click()
    await expect.poll(async () => tallyNum(reviews.tallyKept)).toBe(beforeKept + 1)

    // Backend receipt confirms the mapping was written through.
    await expect.poll(async () => (await receiptOf(apiContext, rid)).kept).toBe(1)
    expect((await receiptOf(apiContext, rid)).removed).toBe(1)
  })

  test('Escape closes the review first (back to tag health); a second Escape closes the overlay', async ({ reviews, page }) => {
    await reviews.createReview('woman')
    await expect(reviews.session).toBeVisible()
    await expect(reviews.board).toBeHidden()

    // First Esc: closes just the review, back to the board underneath — the
    // overlay itself stays open.
    await page.keyboard.press('Escape')
    await expect(reviews.session).toBeHidden()
    await expect(reviews.board).toBeVisible()
    await expect(reviews.overlay).toBeVisible()

    // Second Esc, with no active review: closes the whole overlay.
    await page.keyboard.press('Escape')
    await expect(reviews.overlay).toBeHidden()
  })

  // 'close-up' has several suspects, so the queue is still non-empty after the
  // first decision and the decision bar (with Undo) is still there to click.
  test('Undo reverses the last decision and decrements the tally', async ({ reviews, apiContext }) => {
    await reviews.createReview('close-up')
    const rid = await openReviewId(apiContext, 'close-up')
    await expect(reviews.binBanner.first()).toBeVisible()
    const beforeRemoved = await tallyNum(reviews.tallyRemoved)
    await reviews.noButton.click() // remove
    await expect.poll(async () => tallyNum(reviews.tallyRemoved)).toBe(beforeRemoved + 1)

    await reviews.undoButton.click()
    await expect.poll(async () => tallyNum(reviews.tallyRemoved)).toBe(beforeRemoved)
    // Undo empties the stack again -> disabled.
    await expect(reviews.undoButton).toBeDisabled()
    // Backend receipt is back to zero decided.
    expect((await receiptOf(apiContext, rid)).removed).toBe(0)
  })

  test('Skip removes the card with no decision and is reported separately', async ({ reviews, apiContext }) => {
    await reviews.createReview('smile')
    const rid = await openReviewId(apiContext, 'smile')
    const beforeSkipped = await tallyNum(reviews.tallySkipped)
    await reviews.skipButton.click()
    await expect.poll(async () => tallyNum(reviews.tallySkipped)).toBe(beforeSkipped + 1)
    // The skipped count is reported on the session's own tally (asserted
    // above); the board footer no longer lists open reviews.
    // Skip writes nothing to the receipt's decided counts.
    await expect.poll(async () => (await receiptOf(apiContext, rid)).skipped).toBe(beforeSkipped + 1)
    const receipt = await receiptOf(apiContext, rid)
    expect(receipt.removed).toBe(0)
    expect(receipt.added).toBe(0)
    expect(receipt.kept).toBe(0)
  })

  // 'arm hair': a remove card first, then more cards, so every key below acts
  // on a card that is still there.
  test('keyboard Y / N / S / U drive decisions and focus stays on the card', async ({ reviews, page }) => {
    await reviews.createReview('arm hair')
    await expect(reviews.binBanner.first()).toBeVisible()
    await expect(reviews.card).toBeFocused()

    // N (remove) records a removal.
    const beforeRemoved = await tallyNum(reviews.tallyRemoved)
    await page.keyboard.press('n')
    await expect.poll(async () => tallyNum(reviews.tallyRemoved)).toBe(beforeRemoved + 1)
    await expect(reviews.card).toBeFocused()

    // U undoes it, and the card it puts back takes focus.
    await page.keyboard.press('u')
    await expect.poll(async () => tallyNum(reviews.tallyRemoved)).toBe(beforeRemoved)
    await expect(reviews.card).toBeFocused()

    // Y on that same remove card keeps the tag.
    const beforeKept = await tallyNum(reviews.tallyKept)
    await page.keyboard.press('y')
    await expect.poll(async () => tallyNum(reviews.tallyKept)).toBe(beforeKept + 1)
    await expect(reviews.card).toBeFocused()

    // S skips the next card.
    const beforeSkipped = await tallyNum(reviews.tallySkipped)
    await page.keyboard.press('s')
    await expect.poll(async () => tallyNum(reviews.tallySkipped)).toBe(beforeSkipped + 1)
  })

  // Archiving files the review away and returns to the tag-health board to pick
  // the next tag; the completion state has already shown the tally. The archived
  // receipt is opened from the rail's Archived list.
  test('working through the queue reaches completion and archives to a receipt', async ({ reviews, apiContext }) => {
    await reviews.createReview('closed eyes')
    const rid = await openReviewId(apiContext, 'closed eyes')

    // The session mounts before its queue lands: wait for the first card (or
    // the completion state) so the loop below does not read "no button" as done.
    await expect(reviews.noButton.or(reviews.doneState).first()).toBeVisible()
    // Decide every remaining card (No = remove) until the completion state shows.
    for (let i = 0; i < 25; i++) {
      if (await reviews.doneState.isVisible().catch(() => false)) break
      if (!(await reviews.noButton.isVisible().catch(() => false))) break
      await reviews.noButton.click()
      await reviews.page.waitForTimeout(150)
    }
    await expect(reviews.doneState).toBeVisible()
    await expect(reviews.doneState).toContainText('reviewed')
    // Archive from the completion state.
    await expect(reviews.archiveButton).toBeVisible()
    await reviews.archiveButton.click()

    // Archiving lands on the board, and the session leaves the open list.
    await expect(reviews.board).toBeVisible()
    await expect(reviews.session).toBeHidden()
    await expect(reviews.sidebarSession('closed eyes')).toHaveCount(0)

    // The archived receipt opens from the rail and renders with the tally.
    await reviews.railArchived.filter({ hasText: 'closed eyes' }).first().click()
    await expect(reviews.archived).toBeVisible()
    await expect(reviews.archivedTitle).toContainText('closed eyes')
    await expect(reviews.archivedTally).toContainText('removed')
    // Backend confirms ARCHIVED.
    const detail = await (await apiContext.get(`/api/v1/reviews/${rid}`)).json()
    expect(detail.status).toBe('ARCHIVED')
  })

  // The row menu is the sidebar's: Abort and Archive live there, not on the
  // board's footer. A review with a decision made opens the Keep / Undo / Cancel
  // dialog. Cancel changes nothing and focus returns to the row. The keyboard
  // path (Shift+F10, Enter, then Shift+Tab to Keep) aborts it, and focus moves
  // to the Tags row, since the aborted row has gone.
  test('Abort from the sidebar row asks Keep / Undo / Cancel, by mouse and by keyboard', async ({ reviews, apiContext, page }) => {
    await reviews.createReview('beard')
    const rid = await openReviewId(apiContext, 'beard')
    await expect(reviews.noButton.or(reviews.doneState).first()).toBeVisible()
    // One decision, so the dialog has a change to keep or undo.
    if (await reviews.noButton.isVisible().catch(() => false)) await reviews.noButton.click()
    await expect.poll(async () => {
      const r = await receiptOf(apiContext, rid)
      return r.removed + r.added + r.kept
    }).toBeGreaterThan(0)

    // Mouse: right-click the row, Abort review…, then Cancel.
    await reviews.openSidebarMenu('beard')
    await reviews.sidebarAbortItem.click()
    await expect(reviews.abortDialog).toBeVisible()
    await expect(reviews.abortKeep).toContainText('Keep')
    await expect(reviews.abortUndo).toContainText('Undo')
    await reviews.abortCancel.click()
    await expect(reviews.abortDialog).toBeHidden()
    await expect(reviews.sidebarSession('beard')).toBeFocused()
    expect(await openReviewId(apiContext, 'beard')).toBe(rid)

    // Keyboard: focus the row, Shift+F10, Enter on Abort review…, then Shift+Tab
    // twice from the initially focused Cancel to Keep, and Enter.
    await reviews.sidebarSession('beard').focus()
    await page.keyboard.press('Shift+F10')
    await expect(reviews.sidebarAbortItem).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(reviews.abortDialog).toBeVisible()
    await expect(reviews.abortCancel).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Shift+Tab')
    await expect(reviews.abortKeep).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(reviews.abortDialog).toBeHidden()
    await expect(reviews.sidebarSessions.filter({ hasText: 'beard' })).toHaveCount(0)
    await expect(reviews.sidebarRow).toBeFocused()
    await expect.poll(async () => (await (await apiContext.get(`/api/v1/reviews/${rid}`)).json()).status).toBe('ABORTED')
  })

  // Archive is the row menu's other verb: it ends the review keeping its
  // decisions, with no confirm, and files the receipt under Archived.
  test('Archive from the sidebar row ends the review and files its receipt', async ({ reviews, apiContext }) => {
    await reviews.createReview('grin')
    const rid = await openReviewId(apiContext, 'grin')
    await reviews.openSidebarMenu('grin')
    await reviews.sidebarArchiveItem.click()
    await expect(reviews.abortDialog).toBeHidden()
    await expect(reviews.sidebarSessions.filter({ hasText: 'grin' })).toHaveCount(0)
    await expect(reviews.sidebarRow).toBeFocused()
    await expect.poll(async () => (await (await apiContext.get(`/api/v1/reviews/${rid}`)).json()).status).toBe('ARCHIVED')
    await expect(reviews.railArchived.filter({ hasText: 'grin' })).toHaveCount(1)
  })
})
