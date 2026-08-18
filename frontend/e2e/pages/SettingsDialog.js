import { expect } from '@playwright/test'

/**
 * The user settings dialog (UserSettingsDialog.vue / AccountSection.vue).
 * Opened from the toolbar gear (mdi-cog-outline). The restyle moved the dialog
 * onto AppDialog (.app-dialog) with a left nav rail (.settings-nav /
 * .settings-nav-item) instead of Vuetify tabs, a header "Log out" AppButton,
 * and a table-based token list whose create form now lives in its own
 * AppDialog reached via the "New token" button. Selectors verified in source:
 * .settings-nav-item ("Account Settings", "Snapshots"), the "Token description"
 * field, the "New token"/"Create token" buttons, .account-token-table rows, and
 * .snapshots-section / .snapshot-row.
 */
export class SettingsDialog {
  constructor(page) {
    this.page = page
    this.openButton = page.locator('.bar-btn:has(.mdi-cog-outline)').first()
    // The settings instance of AppDialog is the one rendering the nav rail.
    this.card = page.locator('.app-dialog:has(.settings-nav)')
    this.logoutButton = page.getByRole('button', { name: 'Log out' })
    this.accountTab = page.locator('.settings-nav-item', {
      hasText: 'Account Settings',
    })
    // Token creation is a two-step flow now: "New token" opens a create dialog.
    this.newTokenButton = page.getByRole('button', { name: 'New token' })
    this.tokenDescription = page.getByLabel('Token description')
    this.createTokenButton = page.getByRole('button', { name: 'Create token' })
    this.tokenRows = page.locator('.account-token-table tbody tr')
    // Snapshots tab (SnapshotsSection.vue).
    this.snapshotsTab = page.locator('.settings-nav-item', {
      hasText: 'Snapshots',
    })
    this.snapshotsSection = page.locator('.snapshots-section')
    this.snapshotRows = page.locator('.snapshot-row')
  }

  async open() {
    await this.openButton.click()
    await expect(this.card).toBeVisible()
  }

  async openAccountTab() {
    await this.accountTab.click()
    await expect(this.newTokenButton).toBeVisible()
  }

  /** Open the "New API token" create dialog and wait for its form. */
  async openCreateTokenDialog() {
    await this.newTokenButton.click()
    await expect(this.tokenDescription).toBeVisible()
  }

  async openSnapshotsTab() {
    await this.snapshotsTab.click()
    await expect(this.snapshotsSection).toBeVisible()
  }

  /**
   * Open any pane by its rail label ("Compute", "Backend", "Privacy", …).
   * The rail is plain buttons in a <nav>, not a tablist, so getByRole('tab')
   * matches nothing — go through .settings-nav-item, scoped to this dialog.
   *
   * Waits for the item to become aria-current="page" rather than returning on
   * the click: the panes are v-show, so a click that lands on nothing leaves
   * the PREVIOUS pane on screen, and a scene that only sleeps before shooting
   * would capture the wrong one and still pass.
   */
  async openTab(label) {
    const item = this.card
      .locator('.settings-nav-item', { hasText: label })
      .first()
    await item.click()
    await expect(item).toHaveAttribute('aria-current', 'page')
  }
}
