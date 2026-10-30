import { expect } from '@playwright/test'

/**
 * The lightbox, opened inside the frame over the centre column and the right
 * rail. Selectors verified in Lightbox.vue: .lightbox root,
 * .lightbox-back, .lightbox-nav-left/right, .face-bbox-overlay. Info is the
 * grid rail's one-picture view (RailPicture.vue): the faces row (.rail-faces,
 * a .rail-face per face holding its .rail-face__crop) and the tags (RailTags).
 */
export class Lightbox {
  constructor(page) {
    this.page = page
    this.root = page.locator('.lightbox')
    this.mainImage = page.locator('.lightbox-media-inner img').first()
    this.closeButton = page.locator('.lightbox-back')
    this.next = page.locator('.lightbox-nav-right')
    this.prev = page.locator('.lightbox-nav-left')
    this.facesSection = page.locator('.lightbox .rail-faces')
    this.faceCrops = page.locator('.lightbox .rail-face__crop')
    // Face boxes have no global switch: pointing at a face's crop draws its
    // box, clicking it keeps the box on.
    this.faceChips = page.locator('.lightbox .rail-face')
    // The pane stands where the right rail stands, on its own open flag.
    this.pane = page.locator('.lightbox .lightbox-sidebar')
    this.railToggle = page.locator('.lightbox .tb-stats-btn')
    // Every box is in the DOM; only the ones asked about are visible.
    this.faceBboxes = page.locator('.face-bbox-overlay')
    // Tags (§5) — the field T focuses and the chips. Scoped to the applied
    // list (.rail-tags__applied). Removing a tag rejects its prediction, which
    // re-renders the same label as a chip under Rejected
    // (.rail-tags__rejected); that chip is not an applied tag and must not
    // match here, or removeTag assertions go flaky.
    this.tags = page.locator('.lightbox .rail-tags__applied .rail-chip')
    this.rejectedTags = page.locator('.lightbox .rail-tags__rejected .rail-chip')
    this.tagInput = page.locator('.lightbox .rail-tags__input')
    // Star rating (§6) — heading the rail's Info tab. Scoped to .lightbox so
    // it never matches the grid's .star-overlay--compact thumbnails. Filled
    // stars carry an inline colour referencing --v-theme-accent; unfilled ones
    // reference --v-theme-on-background, so filled stars are countable.
    this.starWidget = page.locator('.lightbox .star-overlay').first()
    this.stars = this.starWidget.locator('.v-icon')
    // The inspector's tabs (AppInspector's .inspector-tab) and the Edit tab's
    // Quick changes (rotate and crop), which need no ComfyUI.
    this.editTab = page.locator('.lightbox .inspector-tab', { hasText: 'Edit' })
    this.rotateLeftButton = page.getByRole('button', { name: 'Rotate left', exact: true })
    this.rotateRightButton = page.getByRole('button', { name: 'Rotate right', exact: true })
    this.cropButton = page.locator('.lightbox-quick-actions').getByRole('button', {
      name: 'Crop',
      exact: true,
    })
    // Crop's framings and its apply button float over the picture
    // (PaintOptionsBar.vue's .paint-options strip).
    this.cropOptions = page.locator('.lightbox .paint-options')
    this.applyCropButton = this.cropOptions.getByRole('button', {
      name: 'Crop to a new picture',
    })
    // The description (LightboxDescriptionPanel.vue): one textarea that turns
    // editable on focus, Enter saves, and the regenerate button that resets it.
    this.descriptionField = page.locator('.lightbox .description-editor textarea')
    this.resetDescriptionButton = page.locator(
      '.lightbox button[aria-label^="Regenerate description - deletes"]',
    )
    // The faces pencil and its panel (FacesPanel.vue), one person picker a face.
    this.facesPencil = page.locator('.lightbox button[aria-label$="dit faces"]')
    this.facesPanel = page.getByRole('dialog', { name: 'Faces' })
  }

  /** One crop framing chip, by its label ("Square on face", "3:4 on face"...). */
  cropFraming(label) {
    return this.cropOptions.getByRole('button', { name: label })
  }

  /** Show the Edit tab, where the Quick changes are. */
  async openEditTab() {
    await this.editTab.click()
    await expect(this.editTab).toHaveAttribute('aria-pressed', 'true')
  }

  /** The natural size of the picture on screen, after the file's orientation. */
  mainImageSize() {
    return this.mainImage.evaluate((img) => [img.naturalWidth, img.naturalHeight])
  }

  /** A tag chip located by its visible label. */
  tag(label) {
    return this.tags.filter({ hasText: label }).first()
  }

  /** Add a tag through the tag field (commits on Enter). */
  async addTag(label) {
    await expect(this.tagInput).toBeVisible()
    await this.tagInput.fill(label)
    await this.tagInput.press('Enter')
  }

  /** Remove a tag via the ✕ on its chip. */
  async removeTag(label) {
    const chip = this.tag(label)
    await chip.hover()
    await chip.locator('.rail-chip__x').click()
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

  /** Open the lightbox by clicking a grid thumbnail (first by default). */
  async openFromGrid(cardLocator) {
    const target = cardLocator ?? this.page.locator('.thumbnail-card').first()
    // The grid re-renders on picture websocket events, and the e2e backend runs
    // a thumbnail-upgrade job that emits a steady stream of them for the whole
    // run. That can swap the card element out between Playwright's actionability
    // check and the click itself, so the click lands on a detached node and is
    // silently swallowed — the lightbox never opens and the next assertion times
    // out. Retry the click until the lightbox is actually up, rather than
    // asserting once on a click that may never have registered.
    await expect(async () => {
      // A click selects; a double-click opens (the default open gesture).
      await target.dblclick()
      await expect(this.root).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 30_000 })
    await this.openRail()
  }

  /** Open the rail if it is shut. Its open flag is its own, and remembered. */
  async openRail() {
    if (await this.pane.evaluate((el) => el.classList.contains('inspector--collapsed'))) {
      await this.railToggle.click()
    }
    await expect(this.pane).not.toHaveClass(/inspector--collapsed/)
    // The class flips at once but the rail slides open: a box read before it
    // lands measures a rail a pixel wide.
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
