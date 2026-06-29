import { expect } from '@playwright/test'

/**
 * The full-screen image detail overlay (lightbox). Selectors verified in
 * ImageOverlay.vue: .image-overlay root, .overlay-close, .overlay-nav-left/right,
 * the face-bbox toggle (aria-label), .sidebar-section--faces, .face-assign-crop,
 * .face-bbox-overlay.
 */
export class ImageOverlay {
  constructor(page) {
    this.page = page
    this.root = page.locator('.image-overlay')
    this.mainImage = page.locator('.overlay-media-inner img').first()
    this.closeButton = page.locator('.overlay-close')
    this.next = page.locator('.overlay-nav-right')
    this.prev = page.locator('.overlay-nav-left')
    this.facesSection = page.locator('.sidebar-section--faces')
    this.faceCrops = page.locator('.face-assign-crop')
    this.faceBboxToggle = page.getByRole('button', {
      name: 'Toggle face bounding boxes',
    })
    this.faceBboxes = page.locator('.face-bbox-overlay')
    // Tags (§5) — chips, the "Add tag (T)" affordance, and the inline input.
    // Scoped to the applied-tag list (.tag-section). Removing a tag rejects its
    // prediction, which re-renders the same label as a chip in the separate
    // "Rejected Tags" section (.tag-drop-zone--predictions); that chip is not an
    // applied tag and must not match here, or removeTag assertions go flaky.
    this.tags = page.locator('.tag-section .overlay-tag')
    this.addTagButton = page.locator('.section-meta-btn[title="Add tag (T)"]').first()
    this.tagInput = page.locator('.tag-add-input')
    // Star rating (§6) — the overlay topbar widget. Scoped to .image-overlay so
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
    await target.click()
    await expect(this.root).toBeVisible()
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
