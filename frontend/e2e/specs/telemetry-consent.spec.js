import { test, expect } from '../fixtures/test.js'

const OPTED_OUT = {
  check_for_updates: false,
  telemetry_send_install_id: false,
  telemetry_send_feature_usage: false,
  telemetry_send_error_reports: false,
  telemetry_send_hardware_profile: false,
  telemetry_consent_prompted: true,
}

test('telemetry consent preview stays compact while choices are explored', async ({
  apiContext,
  page,
}) => {
  // Use the upgrade-with-checks variant: its second choice shows both requests
  // and therefore exercises the preview's largest payload.
  const showPrompt = await apiContext.patch('/api/v1/users/me/config', {
    data: {
      ...OPTED_OUT,
      check_for_updates: true,
      telemetry_consent_prompted: false,
    },
  })
  expect(showPrompt.ok()).toBe(true)

  try {
    await page.goto('/')
    const dialog = page.locator('.tc')
    const preview = dialog.locator('.tp')
    const choices = dialog.locator('.tc__opt')
    await expect(dialog).toBeVisible()
    await expect(choices).toHaveCount(2)

    // Vuetify scales the overlay content as it opens. Measure only after that
    // transition, otherwise the first bounding box is deliberately smaller.
    await page.waitForTimeout(300)
    const initialHeight = await dialog.evaluate((el) => el.getBoundingClientRect().height)

    for (const choice of await choices.all()) {
      await choice.hover()
      await expect
        .poll(() => dialog.evaluate((el) => el.getBoundingClientRect().height))
        .toBe(initialHeight)
      const sizes = await preview.evaluate((el) => ({
        clientHeight: el.clientHeight,
        scrollHeight: el.scrollHeight,
      }))
      expect(sizes.scrollHeight).toBeLessThanOrEqual(sizes.clientHeight)
    }
  } finally {
    const restore = await apiContext.patch('/api/v1/users/me/config', {
      data: OPTED_OUT,
    })
    expect(restore.ok()).toBe(true)
  }
})
