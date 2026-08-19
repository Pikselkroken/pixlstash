import { expect } from '@playwright/test'

/**
 * The main image grid + toolbar. Thin wrapper around the stable selectors
 * verified in ImageGrid.vue / Toolbar.vue. Centralises selectors so a future
 * Vue refactor only touches this file. Holds no assertions of its own beyond
 * the readiness wait.
 *
 * Pictures are identified by their loaded thumbnail src — getThumbnailSrc()
 * returns a URL of the form .../pictures/thumbnails/<id>.webp, which is the
 * only per-card picture identifier the grid renders to the DOM.
 */
export class GridPage {
  constructor(page) {
    this.page = page
    this.gridRoot = page.getByTestId('image-grid')
    this.grid = page.locator('.image-grid')
    this.scrollWrapper = page.locator('.grid-scroll-wrapper')
    this.cards = page.locator('.image-card')
    this.thumbnails = page.locator('.thumbnail-card')
    this.thumbnailImages = page.locator('.thumbnail-img')
    // Toolbar buttons (titles are stable, verified in Toolbar.vue).
    this.searchButton = page.locator('button[title="Search (F)"]').first()
    // Sort is a split-button: .bar-split-menu opens the sort popover
    // (.gb-sort-panel); the in-popover ghost button flips the direction.
    this.sortMenuButton = page.locator('.bar-split-menu').first()
    this.sortDirectionButton = page.locator('.gb-sort-panel .tbm-ghost').first()
    this.viewMenuButton = page.locator('button[title="View options"]').first()
    this.columnsSlider = page.locator('.gb-columns-slider')
    // Expand/Collapse-all live in the View popover as .tbm-action buttons.
    this.expandAllStacksButton = page
      .locator('.tbm-action', { hasText: 'Expand all' })
      .first()
    this.collapseAllStacksButton = page
      .locator('.tbm-action', { hasText: 'Collapse all' })
      .first()
    // Search popover (.gb-search-panel) opened from the toolbar search icon.
    this.searchOverlay = page.locator('.gb-search-panel')
    this.searchInput = page.locator('.gb-search-panel input').first()
    this.searchHistoryChips = page.locator('.gb-recent-row')
    // Right-click context menu (§3.5) — ImageGridContextMenu.vue.
    this.contextMenu = page.locator('.image-ctx-menu')
    // Selection ▾ dropdown — SelectionBar.vue's activator (`.stack-btn` inside
    // `.selection-ctx-bar`) opening SelectionMenu.vue's `.selection-menu-panel`.
    // The activator is disabled until something is selected.
    this.selectionMenuButton = page.locator('.selection-ctx-bar .stack-btn')
    this.selectionMenuPanel = page.locator('.selection-menu-panel')
    this.selectionCountLabel = page.locator(
      '.selection-ctx-bar .bar-btn-apply-label',
    )
    // Statistics sidebar — toggled from the toolbar. Its title flips with state
    // ("Show"/"Hide stats sidebar"), so target the (single) chart-bar button by
    // icon, which is stable across both states.
    this.statsToggle = page.locator('.bar-btn:has(.mdi-chart-bar)').first()
    this.statsSidebar = page.locator('.stats-sidebar')
    this.statsContent = page.locator('.stats-sidebar-content')
    this.statsTabs = page.locator('.stats-tab-btn')
    // Boolean set-operation toolbar (appears when >1 set/character selected).
    this.multiSelectToolbar = page.locator('.multi-select-toolbar')
    this.multiSelectMode = page.locator('.multi-select-toolbar__mode')
    this.multiSelectLabel = page.locator('.multi-select-toolbar__label')
    this.multiSelectClear = page.locator('.multi-select-toolbar__clear')
  }

  async goto() {
    await this.page.goto('/')
    await this.waitForLoaded()
  }

  async waitForLoaded() {
    await expect(this.thumbnails.first()).toBeVisible({ timeout: 15_000 })
    // A visible `.thumbnail-card` is only the placeholder: the grid is still
    // filling and re-rendering at that point, so an interaction started here can
    // land on a card that is about to be replaced and be silently swallowed.
    // Waiting for a real thumbnail image settles the grid before a spec touches
    // it, which is what most of the intermittent "click did nothing" failures
    // in this suite came down to.
    await expect(this.thumbnailImages.first()).toBeVisible({ timeout: 15_000 })
  }

  /** Wait until a real thumbnail image (not the loading placeholder) renders. */
  async waitForThumbnailLoaded() {
    await expect(this.thumbnailImages.first()).toBeVisible({ timeout: 15_000 })
  }

  /** Raw src of the first rendered thumbnail, cache token and all. */
  firstThumbnailSrc() {
    return this.thumbnailImages.first().getAttribute('src')
  }

  /**
   * Which picture leads the grid — the thumbnail URL without its `?v=` token.
   *
   * Every caller here is asking "did the grid move?", and the picture id is in
   * the path; the token is a cache-buster that legitimately changes underneath
   * a stationary grid. The card renders immediately with a placeholder token
   * derived from `imported_at` and adopts the server's the moment the batch
   * thumbnail POST answers — and the server's is `0` until a picture has been
   * processed, so an unprocessed fixture picture flips token mid-load without
   * the grid moving at all. Comparing raw srcs made that a race: two specs
   * failed their first attempt and passed on retry.
   *
   * Stripping it also makes the *negative* assertions honest. `grid-browse`
   * asserts the leader CHANGED after a sort; on raw srcs a token flip alone
   * satisfies that, so the check could pass while the grid sat still.
   */
  async firstThumbnailKey() {
    const src = await this.firstThumbnailSrc()
    return src === null ? null : src.split('?')[0]
  }

  // --- Grid-refresh pills (data-testid'd in ImageGrid.vue) ----------------
  // Two distinct pills share the .pending-imports-pill class, so they are only
  // distinguishable by testid:
  //   - "New pictures" pill: raised on an external/foreign `added` event.
  //   - "View changed externally" pill: raised on an external `updated` event
  //     that affects the current sort/filter.

  /** The "↑ N new pictures, click to load" pill. */
  pendingImportsPill() {
    return this.page.getByTestId('pending-imports-pill')
  }

  /** The "⟳ View changed externally, click to refresh" pill. */
  sortChangedPill() {
    return this.page.getByTestId('sort-changed-pill')
  }

  /** Either pill, for an "any pill appeared" assertion. */
  anyPill() {
    return this.page.locator(
      '[data-testid="pending-imports-pill"], [data-testid="sort-changed-pill"]',
    )
  }

  /**
   * Assert that neither pill appears within `timeout` ms. Pills are raised
   * synchronously when a foreign WS event is processed, so a short, bounded
   * wait is enough: if the event has been delivered and the pill is going to
   * appear, it will be present well before this resolves. Used by the
   * own-change tests (own change must reconcile silently, never pill).
   */
  async expectNoPill(timeout = 2500) {
    await expect(this.pendingImportsPill()).toHaveCount(0, { timeout })
    await expect(this.sortChangedPill()).toHaveCount(0)
  }

  /** Locate a grid card by picture id via its thumbnail URL. */
  card(pictureId) {
    return this.page
      .locator(`.image-card:has(img[src*="/thumbnails/${pictureId}."])`)
      .first()
  }

  /** Open the Sort dropdown (the split-button beside the sort label). */
  async openSortMenu() {
    await this.sortMenuButton.click()
    await expect(
      this.page.locator('.gb-sort-panel .tbm-toggle').first(),
    ).toBeVisible()
  }

  /** Open the View-options dropdown (columns slider, stack expand/collapse). */
  async openViewMenu() {
    await this.viewMenuButton.click()
    await expect(this.columnsSlider).toBeVisible()
  }

  sortOption(label) {
    return this.page
      .locator('.gb-sort-panel .tbm-toggle', { hasText: label })
      .first()
  }

  /** Right-click a card (first by default) to open the context menu. */
  async openContextMenu(cardLocator) {
    const target = cardLocator ?? this.cards.first()
    await target.click({ button: 'right' })
    await expect(this.contextMenu).toBeVisible()
  }

  /** A context-menu action item by its visible label. */
  contextMenuItem(label) {
    return this.contextMenu.locator('.ctx-item', { hasText: label }).first()
  }

  /**
   * Ctrl-click the first `count` cards to build a multi-picture selection.
   *
   * Ctrl (or Meta) toggles selection instead of opening the lightbox — see
   * handleImageCardClick/handleThumbnailClick in ImageGrid.vue. The click
   * targets `.thumbnail-card`, which stops propagation and owns the real
   * handler; clicking the outer `.image-card` would work too but is a larger
   * hit area that overlaps stack affordances.
   *
   * Returns the number selected, confirmed from the SelectionBar's own count
   * label rather than a CSS class, so the assertion rides on what the user
   * actually sees.
   */
  async selectCards(count) {
    const available = await this.thumbnails.count()
    const target = Math.min(count, available)
    for (let i = 0; i < target; i += 1) {
      await this.thumbnails.nth(i).click({ modifiers: ['ControlOrMeta'] })
    }
    await expect(this.selectionCountLabel).toHaveText(`${target} selected`, {
      timeout: 5_000,
    })
    return target
  }

  /** Open the Selection ▾ dropdown from the toolbar activator. */
  async openSelectionMenu() {
    await expect(this.selectionMenuButton).toBeEnabled()
    await this.selectionMenuButton.click()
    await expect(this.selectionMenuPanel).toBeVisible()
  }

  /**
   * Close the Selection ▾ dropdown, leaving the selection intact.
   *
   * Escape is what a user presses, and it is the only dismissal that does not
   * risk toggling a card: clicking outside the panel lands on the grid, which
   * would clear the selection this spec is about to reuse.
   */
  async closeSelectionMenu() {
    await this.page.keyboard.press('Escape')
    await expect(this.selectionMenuPanel).toBeHidden()
  }

  /**
   * Right-click an already-selected card so the context menu is driven by the
   * same selection as the Selection ▾ dropdown. Right-clicking an unselected
   * card would replace the selection with that one picture and quietly turn a
   * multi-select comparison into a single-select one.
   */
  async openContextMenuOnSelected(index = 0) {
    await this.thumbnails.nth(index).click({ button: 'right' })
    await expect(this.contextMenu).toBeVisible()
  }

  /** Dismiss the context menu. */
  async closeContextMenu() {
    await this.page.keyboard.press('Escape')
    await expect(this.contextMenu).toBeHidden()
  }
}
