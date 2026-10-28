import { test, expect } from '../fixtures/test.js'

// The grid's rail as the selection's inspector (#2161). Selecting in the grid
// changes what the rail is about; its heart and the tile's are one heart; a
// section collapsed for one picture stays collapsed for the next.

/** The picture id behind a grid thumbnail (`…/thumbnails/<id>.webp`). */
async function pictureIdOf(thumbnail) {
  const src = await thumbnail.locator('.thumbnail-img').getAttribute('src')
  return Number(src.match(/thumbnails\/(\d+)\.webp/)[1])
}

test.describe('selection rail', () => {
  test('names the selection, shares the heart with the tile, keeps sections collapsed', async ({
    page,
    grid,
    apiContext,
  }) => {
    await grid.goto()
    if ((await grid.statsContent.count()) === 0) await grid.statsToggle.click()
    const rail = grid.statsSidebar
    const title = rail.getByTestId('rail-title')
    await expect(title).toHaveText('All Pictures')

    const first = grid.thumbnails.nth(0)
    const id = await pictureIdOf(first)
    // Start unfavourited, so the click below can only ever turn it on.
    const reset = await apiContext.post('/api/v1/pictures/favourite', {
      data: { picture_ids: [id], favourite: false },
    })
    expect(reset.ok()).toBe(true)
    await page.reload()
    await grid.waitForLoaded()

    await grid.selectCards(1)
    // One picture: named by what it shows, never by its file name.
    const file = rail
      .locator('.rail-picture__props dt', { hasText: 'File' })
      .locator('xpath=following-sibling::dd[1]')
    await expect(file).not.toHaveText('')
    await expect(title).not.toHaveText('')
    expect(await title.textContent()).not.toContain(
      (await file.textContent()).trim(),
    )

    // The rail's heart is the tile's heart.
    const railHeart = rail.locator('.star-overlay__heart')
    const tileHeart = first.locator('.star-overlay__heart')
    await railHeart.click()
    await expect(railHeart).toHaveAttribute('aria-pressed', 'true')
    await expect(tileHeart).toHaveAttribute('aria-pressed', 'true')
    // And H on the grid reaches the rail.
    await page.locator('body').press('h')
    await expect(railHeart).toHaveAttribute('aria-pressed', 'false')
    await expect(tileHeart).toHaveAttribute('aria-pressed', 'false')

    // Collapse Properties, go through two pictures and back to one.
    await rail.getByRole('button', { name: 'Collapse Properties' }).click()
    await expect(rail.locator('.rail-picture__props')).toHaveCount(0)
    await grid.thumbnails.nth(1).click({ modifiers: ['ControlOrMeta'] })
    await expect(title).toHaveText('2 pictures')
    await first.click({ modifiers: ['ControlOrMeta'] })
    await expect(
      rail.getByRole('button', { name: 'Expand Properties' }),
    ).toBeVisible()
    await expect(rail.locator('.rail-picture__props')).toHaveCount(0)

    // Clear hands the rail back to the view.
    await rail.getByRole('button', { name: 'Clear' }).click()
    await expect(title).toHaveText('All Pictures')
  })

  // Closed, the rail says a selection is waiting behind it and then gets out
  // of the way (visual-language "Closed inspector").
  test('closed, shows an edge tab on a new selection that hides again', async ({
    page,
    grid,
  }) => {
    await grid.goto()
    if ((await grid.statsContent.count()) > 0) await grid.statsToggle.click()
    await expect(grid.statsContent).toHaveCount(0)
    const tab = page.getByTestId('inspector-edge-tab')
    await expect(tab).toBeHidden()

    await grid.selectCards(1)
    await expect(tab).toBeVisible()
    await expect(tab).toHaveAttribute('aria-label', /^Show stats: \S/)
    await expect(tab).toBeHidden({ timeout: 8000 })

    await grid.thumbnails.nth(1).click({ modifiers: ['ControlOrMeta'] })
    await expect(tab).toBeVisible()
    await expect(tab).toHaveAttribute('aria-label', 'Show stats: 2 pictures')
    await tab.click()
    await expect(grid.statsContent).toHaveCount(1)
    await expect(tab).toHaveCount(0)
    await expect(page.locator('.stats-sidebar :focus')).toHaveCount(1)
  })
})
