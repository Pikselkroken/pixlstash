import { expect } from '@playwright/test'

/**
 * The picture/entity share dialog (ShareDialog.vue), opened from the grid
 * context menu's "Share image" action. Selectors verified in source:
 * .share-dialog-card root, the "Create Link" button, .share-dialog-url (the
 * generated read-only link), and a Close/Cancel button.
 */
export class ShareDialog {
  constructor(page) {
    this.page = page
    this.card = page.locator('.share-dialog-card')
    this.createButton = this.card.getByRole('button', { name: 'Create Link' })
    this.url = page.locator('.share-dialog-url')
    this.closeButton = this.card.getByRole('button', { name: /^(Close|Cancel)$/ })
  }

  /** Generate the share link and wait for the URL to appear. */
  async createLink() {
    await this.createButton.click()
    await expect(this.url).toBeVisible()
  }
}
