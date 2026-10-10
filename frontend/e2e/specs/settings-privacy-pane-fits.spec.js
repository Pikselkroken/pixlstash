import { test, expect } from '../fixtures/test.js'

// The Privacy pane is a fixed 524px (.settings-content). With both ghost rows
// showing it must still fit without a vertical scrollbar, whichever "Keep
// picture ghosts" position is chosen: each position swaps in its own sub-text,
// and they do not wrap to the same number of lines.
test('Privacy pane fits without scrolling at every ghost retention position', async ({
  page,
  settings,
}) => {
  await page.goto('/')
  await settings.open()
  await settings.openTab('Privacy')
  // The rows that make the pane tall: without them the check proves nothing.
  await expect(page.getByText('Model ghosts', { exact: true })).toBeVisible()
  await expect(page.getByText('Picture ghosts', { exact: true })).toBeVisible()

  const content = page.locator('.settings-content')
  const keep = page.getByRole('radiogroup', { name: 'Keep picture ghosts' })
  // Each click saves to the shared backend, so end on the default.
  for (const position of ['Never', 'Always', 'While matched']) {
    const radio = keep.getByRole('radio', { name: position, exact: true })
    await radio.click()
    await expect(radio).toHaveAttribute('aria-checked', 'true')
    const { scroll, client } = await content.evaluate((el) => ({
      scroll: el.scrollHeight,
      client: el.clientHeight,
    }))
    expect(scroll, `Privacy pane scrolls at "${position}"`).toBeLessThanOrEqual(
      client,
    )
  }
})
