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
    // Tags (used in later phases)
    this.tags = page.locator('.overlay-tag')
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
