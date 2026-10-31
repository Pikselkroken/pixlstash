import { test, expect } from '../fixtures/test.js'

// Shared copies has its own pane because Privacy has no room for it (#2417).
// It must fit the fixed 524px box too, and its rows must read Strips until a
// way out is told to keep the workflow.
test('Sharing pane fits without scrolling and strips by default', async ({
  page,
  settings,
}) => {
  await page.goto('/')
  await settings.open()
  await settings.openTab('Sharing')

  for (const label of ['Copy', 'Drag out', 'Share panel and Export']) {
    // Vuetify's switch is a checkbox to assistive tech.
    const toggle = page.getByRole('checkbox', {
      name: `${label}: keep the workflow`,
    })
    await expect(toggle).toBeVisible()
    await expect(toggle).not.toBeChecked()
  }
  await expect(page.getByText('Strips', { exact: true })).toHaveCount(3)

  const { scroll, client } = await page
    .locator('.settings-content')
    .evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
  expect(scroll, 'Sharing pane scrolls').toBeLessThanOrEqual(client)
})
