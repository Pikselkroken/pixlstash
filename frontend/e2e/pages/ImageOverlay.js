import { expect } from '@playwright/test'

/**
 * The lightbox, opened inside the frame over the centre column and the right
 * rail. Selectors verified in ImageOverlay.vue: .image-overlay root,
 * .overlay-back, .overlay-nav-left/right, .sidebar-section--faces, the face
 * chips (.face-chip, holding .face-assign-crop), .face-bbox-overlay.
 */
export class ImageOverlay {
  constructor(page) {
    this.page = page
    this.root = page.locator('.image-overlay')
    this.mainImage = page.locator('.overlay-media-inner img').first()
    this.closeButton = page.locator('.overlay-back')
    this.next = page.locator('.overlay-nav-right')
    this.prev = page.locator('.overlay-nav-left')
    this.facesSection = page.locator('.sidebar-section--faces')
    this.faceCrops = page.locator('.face-assign-crop')
    // There are no box switches: pointing at a face's chip draws its box.
    this.faceChips = page.locator('.face-chip')
    // The pane is the app's right rail: it opens and shuts with the grid's.
    this.pane = page.locator('.image-overlay .overlay-sidebar')
    this.railToggle = page.locator('.image-overlay .tb-stats-btn')
    // Every box is in the DOM; only the ones asked about are visible.
    this.faceBboxes = page.locator('.face-bbox-overlay')
    // Tags (§5) — chips, the "Add tag (T)" affordance, and the inline input.
    // Scoped to the applied-tag list (.tag-section). Removing a tag rejects its
    // prediction, which re-renders the same label as a chip in the separate
    // "Rejected Tags" section (.tag-drop-zone--predictions); that chip is not an
    // applied tag and must not match here, or removeTag assertions go flaky.
    this.tags = page.locator('.tag-section .overlay-tag')
    this.addTagButton = page.locator('.section-meta-btn[aria-label="Add tag (T)"]').first()
    this.tagInput = page.locator('.tag-add-input')
    // Star rating (§6) — heading the rail's Info tab. Scoped to .image-overlay so
    // it never matches the grid's .star-overlay--compact thumbnails. Filled
    // stars carry an inline colour referencing --v-theme-accent; unfilled ones
    // reference --v-theme-on-background, so filled stars are countable.
    this.starWidget = page.locator('.image-overlay .star-overlay').first()
    this.stars = this.starWidget.locator('.v-icon')
  }

  /** A tag chip located by its visible label. */
  tag(label) {
    return this.tags.filter({ hasText: label }).first()
  }

  /** Add a tag through the inline input (commits on Enter). */
  async addTag(label) {
    await this.addTagButton.click()
    await expect(this.tagInput).toBeVisible()
    await this.tagInput.fill(label)
    await this.page.keyboard.press('Enter')
  }

  /** Remove a tag via the ✕ delete button on its chip. */
  async removeTag(label) {
    const chip = this.tag(label)
    await chip.hover()
    await chip.locator('.tag-delete-btn').click()
  }

  /** Click the Nth star (1-based) in the overlay rating widget. */
  async setRating(n) {
    await this.stars.nth(n - 1).click()
  }

  /** Count of filled stars, derived from the accent-coloured inline style. */
  filledStarCount() {
    return this.stars.evaluateAll(
      (els) =>
        els.filter((e) => (e.getAttribute('style') || '').includes('--v-theme-accent'))
          .length,
    )
  }

  /** Open the overlay by clicking a grid thumbnail (first by default). */
  async openFromGrid(cardLocator) {
    const target = cardLocator ?? this.page.locator('.thumbnail-card').first()
    // The grid re-renders on picture websocket events, and the e2e backend runs
    // a thumbnail-upgrade job that emits a steady stream of them for the whole
    // run. That can swap the card element out between Playwright's actionability
    // check and the click itself, so the click lands on a detached node and is
    // silently swallowed — the overlay never opens and the next assertion times
    // out. Retry the click until the overlay is actually up, rather than
    // asserting once on a click that may never have registered.
    await expect(async () => {
      // A click selects; a double-click opens (the default open gesture).
      await target.dblclick()
      await expect(this.root).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 30_000 })
    await this.openRail()
  }

  /** Open the rail if it is shut. Its open flag is the grid's, and remembered. */
  async openRail() {
    if (await this.pane.evaluate((el) => el.classList.contains('inspector--collapsed'))) {
      await this.railToggle.click()
    }
    await expect(this.pane).not.toHaveClass(/inspector--collapsed/)
    // The class flips at once but the rail slides open, and the grid's rail,
    // which shares the flag, slides with it: a box read before both land
    // measures a rail a pixel wide.
    await this.page.evaluate(() =>
      Promise.all(
        document
          .getAnimations()
          .filter((a) => a instanceof CSSTransition)
          .map((a) => a.finished.catch(() => null)),
      ),
    )
  }

  /** The toolbar's rail toggle, in the grid toolbar's place. */
  async toggleRail() {
    await this.railToggle.click()
  }

  async close() {
    await this.closeButton.click()
    await expect(this.root).toBeHidden()
  }

  async showNext() {
    await this.page.keyboard.press('ArrowRight')
  }

  async showPrev() {
    await this.page.keyboard.press('ArrowLeft')
  }
}
