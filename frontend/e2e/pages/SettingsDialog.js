import { expect } from '@playwright/test'

/**
 * The user settings dialog (UserSettingsDialog.vue / AccountSection.vue).
 * Opened from the toolbar gear (mdi-cog-outline). Selectors verified in source:
 * .settings-dialog-card, .settings-logout-btn, the "Account Settings" v-tab,
 * the "Token description" field, the "Create Token" button, .settings-token-row.
 */
export class SettingsDialog {
  constructor(page) {
    this.page = page
    this.openButton = page.locator('.bar-btn:has(.mdi-cog-outline)').first()
    this.card = page.locator('.settings-dialog-card')
    this.logoutButton = page.locator('.settings-logout-btn')
    this.accountTab = page.getByRole('tab', { name: 'Account Settings' })
    this.tokenDescription = page.getByLabel('Token description')
    this.createTokenButton = page.getByRole('button', { name: 'Create Token' })
    this.tokenRows = page.locator('.settings-token-row')
  }

  async open() {
    await this.openButton.click()
    await expect(this.card).toBeVisible()
  }

  async openAccountTab() {
    await this.accountTab.click()
    await expect(this.tokenDescription).toBeVisible()
  }
}
