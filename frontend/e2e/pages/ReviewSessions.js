import { expect } from '@playwright/test'

/**
 * The Review Sessions overlay (tag-review redesign): the tag-health board, the
 * new-review dialog, the session rail, and the binary/pair decision cards.
 * Thin wrapper around the stable selectors verified in
 * ReviewSessionsOverlay.vue + components/reviews/*.vue. Holds no assertions of
 * its own beyond readiness waits.
 *
 * The overlay opens from a Pinia store flag (reviewSessionsStore.overlayOpen),
 * toggled by the toolbar button title="Review and fix tags". There are no
 * data-testid attributes in the feature; selectors are class/title/kbd based.
 *
 * The new-review dialog's tag chips are the health-board rows, so the health
 * cache MUST be built before a review can be created through the UI. Use
 * ensureHealthBuilt(apiContext) in a spec's beforeEach (it rebuilds + polls via
 * the API, which is fast on the fixture vault) BEFORE opening the overlay.
 */
export class ReviewSessions {
  constructor(page) {
    this.page = page
    // Launch + shell
    this.launchButton = page.locator('button[title="Review and fix tags"]').first()
    this.overlay = page.locator('.rs-overlay')
    this.shell = page.locator('.rs-shell')

    // Board
    this.board = page.locator('.rs-board')
    this.boardTitle = page.locator('.rs-board-title')
    this.boardRows = page.locator('.rs-board-row:not(.rs-board-row--head)')
    this.boardHeadRow = page.locator('.rs-board-row--head')
    this.sortableHeaders = page.locator('.rs-board-row--head button.rs-board-hdr')
    this.filterInput = page.locator('.rs-board-filter-input')
    this.anomalyToggle = page.locator('.rs-board-anomaly-toggle')
    this.disputesToggle = page.locator('.rs-board-disputes')
    this.sortSelect = page.locator('select.rs-board-sort')
    this.buildingBar = page.locator('.rs-board-building')
    this.rebuildButton = page.locator('.rs-board-rebuild')
    this.persistentRebuildButton = page.locator('.rs-board-rebuild-persistent')
    this.boardEmpty = page.locator('.rs-board-empty')

    // New-review dialog
    this.dialog = page.locator('.rs-dialog[role="dialog"]')
    this.dialogSearch = page.locator('.rs-dialog-search input')
    this.dialogChips = page.locator('.rs-dialog-chip')
    this.dialogError = page.locator('.rs-dialog-error')
    this.dialogInclude = page.locator('.rs-dialog-include input[type="checkbox"]')
    this.dialogCreate = page.locator('.rs-dialog-btn--go')
    this.dialogCancel = page.locator('.rs-dialog-btn', { hasText: 'Cancel' })

    // Rail
    this.rail = page.locator('nav.rs-rail')
    this.railClose = page.locator('.rs-rail-close')
    this.railBoardTab = page.locator('.rs-rail-board')
    this.railSessions = page.locator('.rs-rail-session')
    this.railNewReview = page.locator('.rs-rail-new')
    this.railArchived = page.locator('.rs-rail-archived')
    this.shelf = page.locator('.rs-shelf')
    this.shelfStickers = page.locator('.rs-shelf .rs-sticker')

    // Session view
    this.session = page.locator('.rs-session')
    this.sessionTitle = page.locator('.rs-session-title')
    this.sessionReceipt = page.locator('.rs-session-receipt')
    this.tally = page.locator('.rs-session-tally')
    this.tallyRemoved = page.locator('.rs-tally-removed')
    this.tallyAdded = page.locator('.rs-tally-added')
    this.tallyKept = page.locator('.rs-tally-kept')
    this.tallySkipped = page.locator('.rs-tally-skipped')
    this.stale = page.locator('.rs-session-stale')
    this.refreshButton = page.locator('.rs-session-refresh')
    this.xpPill = page.locator('.rs-xp-pill')
    this.xpStreak = page.locator('.rs-xp-streak')
    this.doneState = page.locator('.rs-state--done')
    this.archiveButton = page.locator('.rs-state-btn--archive')

    // Cards
    this.card = page.locator('.rs-card')
    this.binCard = page.locator('.rs-bin')
    this.binQuestion = page.locator('.rs-bin-question')
    this.binBanner = page.locator('.rs-bin-banner')
    this.binNew = page.locator('.rs-bin-new')
    this.regionToggle = page.locator('.rs-region-toggle')
    this.manualTagOnCard = page.locator('.rs-manual-tag')
    this.pairCard = page.locator('.rs-pair')
    this.pairPanes = page.locator('.rs-pair-pane')
    this.pairNew = page.locator('.rs-pair-new')

    // Decision bar
    this.decideBar = page.locator('.rs-decide[role="toolbar"]')
    this.yesButton = page.locator('.rs-decide-btn--yes')
    this.noButton = page.locator('.rs-decide-btn--no')
    this.skipButton = page.locator('.rs-decide-btn', { has: page.locator('kbd', { hasText: 'S' }) })
    this.undoButton = page.locator('.rs-decide-btn', { has: page.locator('kbd', { hasText: 'U' }) })
    this.gamifyToggle = page.locator('.rs-gamify input[type="checkbox"]')

    // Manual-tag overlay button + panel
    this.tagApplyButton = page.locator('.rs-tag-apply-btn')
    this.tagApplyPanel = page.locator('.rs-tag-apply-panel')

    // Abort dialog
    this.abortDialog = page.locator('.rs-abort[role="dialog"]')
    this.abortKeep = page.locator('.rs-abort-btn--keep')
    this.abortUndo = page.locator('.rs-abort-btn--undo')
    this.abortCancel = page.locator('.rs-abort', { hasText: 'Cancel' }).locator('.rs-abort-btn', { hasText: 'Cancel' })

    // Archived receipt
    this.archived = page.locator('.rs-archived')
    this.archivedTitle = page.locator('.rs-archived-title')
    this.archivedTally = page.locator('.rs-archived-tally')
    this.archivedBack = page.locator('.rs-archived-back')
  }

  /** Open the overlay from the toolbar and wait for the shell. */
  async open() {
    await expect(this.launchButton).toBeVisible()
    await this.launchButton.click()
    await expect(this.overlay).toBeVisible()
  }

  /** Close via the rail close button. */
  async close() {
    await this.railClose.click()
    await expect(this.overlay).toBeHidden()
  }

  /**
   * Rebuild the tag-health cache through the API and poll until built. The
   * fixture vault builds synchronously in well under a second. Call BEFORE
   * opening the overlay so the store's mount-time fetch sees populated rows
   * (the new-review dialog's tag chips are the health rows).
   */
  static async ensureHealthBuilt(apiContext) {
    await apiContext.post('/api/v1/tag_health/rebuild', { data: {} })
    const deadline = Date.now() + 30_000
    while (Date.now() < deadline) {
      const res = await apiContext.get('/api/v1/tag_health')
      const body = await res.json()
      if (!body.building && body.computed_at) return body
      await new Promise((r) => setTimeout(r, 200))
    }
    throw new Error('tag_health did not finish building within 30s')
  }

  /** A board row locator by tag name (matches the tag-name cell text). */
  boardRow(tag) {
    return this.boardRows.filter({ has: this.page.locator('.rs-board-tag-name', { hasText: tag }) }).first()
  }

  /** Open the new-review dialog from the rail. */
  async openNewReview() {
    await this.railNewReview.click()
    await expect(this.dialog).toBeVisible()
  }

  /**
   * Create a review for `tag` through the dialog and wait for the session view.
   * Assumes the health cache is built (so the tag chip exists).
   */
  async createReview(tag) {
    await this.openNewReview()
    await this.dialogSearch.fill(tag)
    // Pick the exact chip (chip text may append " · open"); match the name span.
    const chip = this.dialogChips.filter({ hasText: tag }).first()
    await expect(chip).toBeVisible()
    await chip.click()
    await expect(this.dialogCreate).toBeEnabled()
    await this.dialogCreate.click()
    await expect(this.session).toBeVisible({ timeout: 15_000 })
  }
}
