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
    this.grid = page.locator('.image-grid')
    this.scrollWrapper = page.locator('.grid-scroll-wrapper')
    this.cards = page.locator('.image-card')
    this.thumbnails = page.locator('.thumbnail-card')
    this.thumbnailImages = page.locator('.thumbnail-img')
    // Toolbar buttons (titles are stable, verified in Toolbar.vue).
    this.searchButton = page.locator('button[title="Search (F)"]').first()
    this.sortMenuButton = page.locator('.bar-split-menu').first()
    this.sortDirectionButton = page.locator('.gb-sort-direction')
    this.viewMenuButton = page.locator('button[title="View options"]').first()
    this.columnsSlider = page.locator('.gb-columns-slider')
    this.expandAllStacksButton = page
      .locator('.gb-stack-toggle-btn', { hasText: 'Expand all' })
      .first()
    this.collapseAllStacksButton = page
      .locator('.gb-stack-toggle-btn', { hasText: 'Collapse all' })
      .first()
    // Search overlay.
    this.searchOverlay = page.locator('.search-overlay')
    this.searchInput = page.locator('.search-overlay input').first()
    this.searchHistoryChips = page.locator('.search-history-chip')
    // Right-click context menu (§3.5) — ImageGridContextMenu.vue.
    this.contextMenu = page.locator('.image-ctx-menu')
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
  }

  /** Wait until a real thumbnail image (not the loading placeholder) renders. */
  async waitForThumbnailLoaded() {
    await expect(this.thumbnailImages.first()).toBeVisible({ timeout: 15_000 })
  }

  /** src of the first rendered thumbnail (encodes the top-left picture id). */
  firstThumbnailSrc() {
    return this.thumbnailImages.first().getAttribute('src')
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
    await expect(this.page.locator('.gb-sort-grid-btn').first()).toBeVisible()
  }

  /** Open the View-options dropdown (columns slider, stack expand/collapse). */
  async openViewMenu() {
    await this.viewMenuButton.click()
    await expect(this.columnsSlider).toBeVisible()
  }

  sortOption(label) {
    return this.page.locator('.gb-sort-grid-btn', { hasText: label }).first()
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
}
