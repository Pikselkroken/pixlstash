import { test, expect } from '../fixtures/test.js'
import { ReviewSessions } from '../pages/ReviewSessions.js'

// Review Sessions — the tag-health board (landing view). Drives the real
// backend: the board is served from the tag_health cache, so beforeEach builds
// it through the API first (fast on the fixture vault), then opens the overlay
// so the store's mount-time fetch sees populated rows.
//
// Assertions are relative (at least one row, toggle state flips) per the harness
// convention, so fixture pruning does not break the suite.

test.describe('review sessions — tag health board', () => {
  test.beforeEach(async ({ grid, reviews, apiContext }) => {
    await grid.goto()
    await ReviewSessions.ensureHealthBuilt(apiContext)
    await reviews.open()
    // Board rows populate after the store's async health fetch resolves; wait
    // for the first data row so per-test row counts are not raced.
    await expect(reviews.boardRows.first()).toBeVisible()
  })

  test('opens from the toolbar and renders the ranked board', async ({ reviews }) => {
    await expect(reviews.board).toBeVisible()
    await expect(reviews.boardTitle).toHaveText('Which tags need review?')
    // The header row carries the redesigned column labels — "Priority", not
    // the old "Est. fixes" (docs/reviews/tag-review-board-redesign-ux-spec.md
    // Spec C), and no "Verified" column (Spec E 7a).
    await expect(reviews.boardHeadRow).toContainText('Tag')
    await expect(reviews.boardHeadRow).toContainText('Priority')
    await expect(reviews.boardHeadRow).not.toContainText('Est. fixes')
    await expect(reviews.boardHeadRow).not.toContainText('Verified')
    await expect(reviews.boardHeadRow).toContainText('Est. wrong')
    await expect(reviews.boardHeadRow).toContainText('Est. missing')
    await expect(reviews.boardHeadRow).toContainText('Mismatch')
    await expect(reviews.boardHeadRow).toContainText('Why it ranks here')
    // At least one tag row rendered.
    expect(await reviews.boardRows.count()).toBeGreaterThan(0)
  })

  test('the persistent rebuild control is visible in the header (Spec B)', async ({
    reviews,
  }) => {
    // Not gated behind the empty-state branch — visible with rows already
    // populated (the beforeEach builds the cache and waits for a row).
    await expect(reviews.persistentRebuildButton).toBeVisible()
    await expect(reviews.persistentRebuildButton).toContainText('Updated')
    await expect(reviews.persistentRebuildButton).toBeEnabled()
  })

  test('the Why column is never blank for a row with a ranking signal', async ({
    reviews,
  }) => {
    // "shirt" carries est_wrong/est_missing/mismatch signal in the fixture
    // (per the mismatch-signal test below), so its Why cell must have text.
    const row = reviews.boardRow('shirt')
    await expect(row).toBeVisible()
    const why = row.locator('.rs-board-why')
    await expect(why).toBeVisible()
    const text = (await why.textContent())?.trim() ?? ''
    expect(text.length).toBeGreaterThan(0)
    // The title attribute mirrors the (possibly truncated) text.
    await expect(why).toHaveAttribute('title', text)
  })

  test('the filter input narrows the visible rows', async ({ reviews }) => {
    const total = await reviews.boardRows.count()
    expect(total).toBeGreaterThan(1)
    // A tag we know is present in the fixture health board.
    await reviews.filterInput.fill('shirt')
    await expect.poll(async () => reviews.boardRows.count()).toBeLessThan(total)
    // Every remaining row matches the filter.
    const names = await reviews.page.locator('.rs-board-tag-name').allInnerTexts()
    expect(names.length).toBeGreaterThan(0)
    for (const n of names) expect(n.toLowerCase()).toContain('shirt')
    // Clearing restores the full list.
    await reviews.filterInput.fill('')
    await expect.poll(async () => reviews.boardRows.count()).toBe(total)
  })

  test('a no-match filter shows the empty state', async ({ reviews }) => {
    await reviews.filterInput.fill('zzz-no-such-tag-zzz')
    await expect(reviews.boardEmpty).toBeVisible()
    await expect(reviews.boardEmpty).toContainText('No tags match')
  })

  test('the anomalies-only toggle flips its pressed state', async ({ reviews }) => {
    await expect(reviews.anomalyToggle).toBeVisible()
    await expect(reviews.anomalyToggle).toHaveAttribute('aria-pressed', 'false')
    await reviews.anomalyToggle.click()
    await expect(reviews.anomalyToggle).toHaveAttribute('aria-pressed', 'true')
    await reviews.anomalyToggle.click()
    await expect(reviews.anomalyToggle).toHaveAttribute('aria-pressed', 'false')
  })

  test('a sortable header toggles the active sort', async ({ reviews }) => {
    // "Est. wrong" is a sortable column header (a <button>).
    const wrongHdr = reviews.sortableHeaders.filter({ hasText: 'Est. wrong' }).first()
    await expect(wrongHdr).toBeVisible()
    await wrongHdr.click()
    await expect(wrongHdr).toHaveClass(/rs-board-hdr--active/)
    // The rows still render after re-sorting.
    expect(await reviews.boardRows.count()).toBeGreaterThan(0)
  })

  test('the sort dropdown re-orders without emptying the board', async ({ reviews }) => {
    await reviews.sortSelect.selectOption('tag')
    expect(await reviews.boardRows.count()).toBeGreaterThan(0)
    await reviews.sortSelect.selectOption('missing')
    expect(await reviews.boardRows.count()).toBeGreaterThan(0)
  })

  test('a row with a mismatch signal shows Start review', async ({ reviews }) => {
    // The fixture's PictureLikeness / stack pairs give several tags a mismatch
    // count; "shirt" is one. Its row exposes a "Start review" action.
    await reviews.filterInput.fill('shirt')
    const row = reviews.boardRow('shirt')
    await expect(row).toBeVisible()
    await expect(row.locator('.rs-board-btn')).toContainText('Start review')
  })
})
