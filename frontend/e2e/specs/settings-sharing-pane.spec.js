import { test, expect } from '../fixtures/test.js'

// Shared copies (#2417) sits under Privacy & sharing, and reads Strips until
// told to keep the workflow.
test('Sharing reads Strips by default', async ({
  page,
  settings,
}) => {
  await page.goto('/')
  await settings.open()
  await settings.openTab('Privacy & sharing')

  // Vuetify's switch is a checkbox to assistive tech.
  const toggle = page.getByRole('checkbox', {
    name: 'Keep the workflow when exporting',
  })
  await expect(toggle).toBeVisible()
  await expect(toggle).not.toBeChecked()
  await expect(page.getByText('Strips', { exact: true })).toBeVisible()
})
