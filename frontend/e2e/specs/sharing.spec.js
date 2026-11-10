import { test, expect } from '../fixtures/test.js'
import { Lightbox } from '../pages/Lightbox.js'

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

  test('a read-only image link restores its lightbox after a full reload', async ({
    browser,
    grid,
    lightbox,
    apiContext,
    baseURL,
  }) => {
    await grid.goto()
    await lightbox.openFromGrid()
    const pictureId = new URL(grid.page.url()).searchParams.get('overlay')
    expect(pictureId).toBeTruthy()

    const created = await apiContext.post('/api/v1/users/me/token', {
      data: {
        description: 'e2e lightbox reload',
        scope: 'READ',
        resource_type: 'picture',
        resource_id: Number(pictureId),
      },
    })
    expect(created.ok()).toBe(true)
    const { token } = await created.json()

    const context = await browser.newContext({ baseURL, storageState: undefined })
    const sharedPage = await context.newPage()
    const sharedLightbox = new Lightbox(sharedPage)
    try {
      await sharedPage.goto(
        `/?token=${encodeURIComponent(token)}&overlay=${encodeURIComponent(pictureId)}`,
      )
      await expect(sharedLightbox.root).toBeVisible({ timeout: 15_000 })
      await expect(sharedLightbox.mainImage).toBeVisible({ timeout: 15_000 })
      await expect(sharedPage.locator('.lightbox-image-error')).toBeHidden()

      await sharedPage.reload()
      await expect(sharedLightbox.root).toBeVisible({ timeout: 15_000 })
      await expect(sharedLightbox.mainImage).toBeVisible({ timeout: 15_000 })
      await expect(sharedPage.locator('.lightbox-image-error')).toBeHidden()
      expect(new URL(sharedPage.url()).searchParams.get('overlay')).toBe(pictureId)
    } finally {
      await context.close()
    }
  })
})
