import { expect } from '@playwright/test'

/**
 * The picture/entity share dialog (ShareDialog.vue), opened from the grid
 * context menu's "Share image" action. Selectors verified in source:
 * the AppDialog holding .share-dialog-hint, the "Create link" button,
 * .share-dialog-url (the generated read-only link), and the footer's
 * Close/Cancel button (the header's own close button is also named "Close").
 */
export class ShareDialog {
  constructor(page) {
    this.page = page
    this.card = page.locator('.app-dialog:has(.share-dialog-hint)')
    this.createButton = this.card.getByRole('button', { name: 'Create link' })
    this.url = page.locator('.share-dialog-url')
    this.closeButton = this.card
      .locator('.app-dialog__footer')
      .getByRole('button', { name: /^(Close|Cancel)$/ })
  }

  /** Generate the share link and wait for the URL to appear. */
  async createLink() {
    await this.createButton.click()
    await expect(this.url).toBeVisible()
  }
}
