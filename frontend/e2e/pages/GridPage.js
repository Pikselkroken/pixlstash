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
}
