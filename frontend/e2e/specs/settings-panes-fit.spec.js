import { test, expect } from '../fixtures/test.js'

async function expectNoScroll(page, label) {
  const { scroll, client } = await page
    .locator('.settings-content')
    .evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
  expect(scroll, `Settings pane "${label}" scrolls`).toBeLessThanOrEqual(client)
}

// The redesign's promise: no pane scrolls. A list that can grow scrolls in its
// own box instead. Every rail item this session shows is checked, so a pane
// added later is held to it too.
test('no Settings pane scrolls', async ({ page, settings }) => {
  await page.goto('/')
  await settings.open()
  const labels = await settings.card
    .locator('.settings-nav-item')
    .allTextContents()
  expect(labels.length).toBeGreaterThan(5)
  for (const label of labels.map((l) => l.trim())) {
    await settings.openTab(label)
    // Let the pane's own fetches land before measuring it.
    await page.waitForLoadState('networkidle')
    await expectNoScroll(page, label)
  }
})

// With the PixlStash Tagger active its settings section is inline at the
// bottom of Tagging, and the pane must still fit.
test('Tagging pane fits while the PixlStash Tagger is active', async ({
  apiContext,
  page,
  settings,
}) => {
  const res = await apiContext.patch('/api/v1/users/me/config', {
    data: { tagger_settings: { active_tag_plugin: 'pixlstash_tagger' } },
  })
  expect(res.ok()).toBe(true)

  await page.goto('/')
  await settings.open()
  await settings.openTab('Tagging')
  await expect(page.getByText('PixlStash Tagger Settings')).toBeVisible()
  await expectNoScroll(page, 'Tagging')
})

// Recovery holds the scrapheap, snapshots and ghosts. With both ghost rows
// showing it must fit at every "Keep picture ghosts" position: each swaps in
// its own sub-text, and they do not wrap to the same number of lines.
test('Recovery fits at every ghost retention position', async ({
  page,
  settings,
}) => {
  await page.goto('/')
  await settings.open()
  await settings.openTab('Recovery')
  // The rows that make the pane tall: without them the check proves nothing.
  await expect(page.getByText('Model ghosts', { exact: true })).toBeVisible()
  await expect(page.getByText('Picture ghosts', { exact: true })).toBeVisible()
  await expect(page.locator('.snapshots-section')).toBeVisible()

  const keep = page.getByRole('radiogroup', { name: 'Keep picture ghosts' })
  // Each click saves to the shared backend, so end on the default.
  for (const position of ['Never', 'Always', 'Partially']) {
    const radio = keep.getByRole('radio', { name: position, exact: true })
    await radio.click()
    await expect(radio).toHaveAttribute('aria-checked', 'true')
    await expectNoScroll(page, `Recovery at ${position}`)
  }
})
