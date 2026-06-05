import { test, expect } from '../fixtures/test.js'

// Picture sharing — the grid context menu's "Share image" action opens the
// share dialog, and "Create Link" mints a read-only link that is shown for
// copying. This adds a scoped READ token to the backend (an additive mutation
// that does not affect other specs).

test.describe('sharing', () => {
  test('creates a read-only share link for a picture', async ({ grid, shareDialog }) => {
    await grid.goto()
    await grid.openContextMenu()
    await grid.contextMenuItem('Share image').click()

    await expect(shareDialog.card).toBeVisible()
    await shareDialog.createLink()

    // A real URL is produced (either a gallery ?token= link or a /share/ file link).
    await expect(shareDialog.url).toContainText('http')

    await shareDialog.closeButton.click()
    await expect(shareDialog.card).toBeHidden()
  })
})
