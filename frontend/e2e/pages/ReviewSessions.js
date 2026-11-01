import { expect } from '@playwright/test'

/**
 * The Tags view: the tag-health board (its controls on the view's
 * toolbar line), the new-review dialog, the open reviews nested under the
 * sidebar's "Tags" row (each with its Abort and Archive menu), and the
 * binary/pair decision cards. The view
 * replaces the grid in the centre column.
 * Thin wrapper around the stable selectors verified in
 * ReviewSessionsOverlay.vue + components/reviews/*.vue. Holds no assertions of
 * its own beyond readiness waits.
 *
 * The view opens from a Pinia store flag (reviewSessionsStore.overlayOpen),
 * which `?review=board` sets (useReviewRoute), as does the sidebar row. The toolbar button that used to
 * toggle it left the browse-only toolbar (#2160). There are no data-testid
 * attributes in the feature; selectors are class/title/kbd based.
 *
 * The new-review dialog's tag chips are the health-board rows, so the health
 * cache MUST be built before a review can be created through the UI. Use
 * ensureHealthBuilt(apiContext) in a spec's beforeEach (it rebuilds + polls via
 * the API, which is fast on the fixture vault) BEFORE opening the overlay.
 */
export class ReviewSessions {
  constructor(page) {
    this.page = page
    // Shell
    this.overlay = page.locator('.rs-overlay')
    this.shell = page.locator('.rs-shell')

    // Board
    this.board = page.locator('.rs-board')
    this.toolbar = page.locator('.rs-toolbar')
    this.toolbarTitle = page.locator('.rs-toolbar-title')
    this.back = page.locator('.rs-toolbar-back')
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

    // The board's footer: archived receipts and the sticker shelf. The open
    // reviews and their Abort / Archive live in the sidebar (below).
    this.rail = page.locator('.rs-rail')
    this.railArchived = page.locator('.rs-rail-archived')
    this.shelf = page.locator('.rs-shelf')
    this.shelfStickers = page.locator('.rs-shelf .rs-sticker')

    // The sidebar: "Tags" and the open reviews nested under it
    this.sidebarRow = page.locator('.sidebar-list-item', { has: page.locator('.sidebar-list-label', { hasText: /^Tags$/ }) })
    this.sidebarSessions = page.locator('.sidebar-review-session:not(.sidebar-review-new)')
    this.sidebarNewReview = page.locator('.sidebar-review-new')
    // A review row's menu (right-click, Menu key or Shift+F10): its verbs.
    this.sidebarCtxMenu = page.locator('.sidebar-ctx-menu')
    this.sidebarAbortItem = this.sidebarCtxMenu.locator('.ctx-item', { hasText: 'Abort review' })
    this.sidebarArchiveItem = this.sidebarCtxMenu.locator('.ctx-item', { hasText: 'Archive review' })

    // Session view
    this.session = page.locator('.rs-session')
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

    // Abort dialog (ReviewAbortDialog, teleported to the body)
    // The abort dialog is AppDialog: a v-dialog overlay, shown while active.
    this.abortDialog = page.locator('.v-overlay--active .app-dialog')
    this.abortKeep = this.abortDialog.locator('.app-dialog__footer button', { hasText: 'Keep' })
    this.abortUndo = this.abortDialog.locator('.app-dialog__footer button', { hasText: 'Undo' })
    this.abortCancel = this.abortDialog.locator('.app-dialog__footer button', { hasText: 'Cancel' })

    // Archived receipt
    this.archived = page.locator('.rs-archived')
    this.archivedTitle = page.locator('.rs-archived-title')
    this.archivedTally = page.locator('.rs-archived-tally')
    this.archivedBack = page.locator('.rs-archived-back')
  }

  /**
   * Open the overlay on the board through its URL and wait for the shell. The
   * board opens on its Groups view by default, so the health table this page
   * object reads is chosen first, through the view's own storage key.
   */
  async open() {
    await this.page.addInitScript(() => {
      try {
        window.localStorage.setItem('pixlstash.tagReview.view', 'health')
      } catch {
        // Storage blocked: the board falls back to its default view.
      }
    })
    const url = new URL(this.page.url())
    url.searchParams.set('review', 'board')
    await this.page.goto(url.toString())
    await expect(this.overlay).toBeVisible()
  }

  /** The sidebar row of an open review, by tag. */
  sidebarSession(tag) {
    return this.sidebarSessions.filter({ hasText: tag }).first()
  }

  /** Open a review row's menu with the mouse (right-click). */
  async openSidebarMenu(tag) {
    await this.sidebarSession(tag).click({ button: 'right' })
    await expect(this.sidebarAbortItem).toBeVisible()
  }

  /** Leave through the toolbar's back button (from the board). */
  async close() {
    await this.back.click()
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

  /** Open the new-review dialog from the sidebar's "New review" row. */
  async openNewReview() {
    await this.sidebarNewReview.click()
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
