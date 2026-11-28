// The Guide (#2825) on the e2e fixture: the title-bar Guide button and F1,
// search, a concept opened in place, a wizard ringing a real control, and the
// welcome with the "seen" it writes to the owner's config.
import { test, expect } from '../fixtures/test.js'
import { WELCOME_EDITION } from '../../src/guide/catalogue.js'

async function openApp(page) {
  await page.goto('/')
  await expect(page.locator('.thumbnail-card').first()).toBeVisible({
    timeout: 15_000,
  })
}

test('the title-bar Guide opens the A–Z grid, and search narrows it', async ({
  page,
}) => {
  await openApp(page)
  await page.getByTestId('titlebar-guide').click()
  await expect(page).toHaveURL(/\/guide$/)
  const view = page.getByTestId('guide-view')
  await expect(view).toBeVisible()
  // New in 2.x is pinned in Start here; every concept is in the A–Z grid.
  await expect(view.getByTestId('guide-card-new')).toBeVisible()
  await expect(view.getByTestId('guide-card-search')).toHaveCount(1)
  // With the Guide open, no workspace segment is lit.
  await expect(page.locator('.titlebar-ws--current')).toHaveCount(0)

  await view.getByTestId('guide-search').locator('input').fill('dedupe')
  await expect(view.getByTestId('guide-card-stacks-duplicates')).toBeVisible()
  await expect(view.getByTestId('guide-card-search')).toHaveCount(0)
  await view.getByTestId('guide-search').locator('input').fill('zzzzqq')
  await expect(view.getByTestId('guide-empty')).toBeVisible()
  await view.getByTestId('guide-search').locator('input').fill('')
})

test('the API concept shows the server reference in place', async ({ page }) => {
  await openApp(page)
  await page.keyboard.press('F1')
  await page.getByTestId('guide-card-api').click()
  await page.getByTestId('guide-view-reference').click()
  await expect(page).toHaveURL(/\/guide\/api\?view=reference/)
  const frame = page.getByTestId('guide-reference')
  await expect(frame).toHaveAttribute('src', /\/scalar$/)
  // The frame holds the server's own page, not a copy in the app.
  await expect
    .poll(async () => (await frame.elementHandle()).contentFrame().then((f) => f.title()))
    .toBe('PixlStash API Reference')
  // And the hash-pinned Scalar bundle loaded and rendered it (a wrong
  // integrity hash leaves the title but blocks the script).
  await expect(
    page.frameLocator('[data-testid="guide-reference"]').locator('[data-v-app]').first(),
  ).toBeAttached({ timeout: 30_000 })

  // A concept with no server page offers no Reference view.
  await page.getByTestId('titlebar-guide').click()
  await page.getByTestId('guide-card-people').click()
  await expect(page.getByTestId('guide-concept')).toBeVisible()
  await expect(page.getByTestId('guide-view-reference')).toHaveCount(0)
})

test('F1 opens the Guide; a concept opens in place and a wizard rings its control', async ({
  page,
}) => {
  await openApp(page)
  await page.keyboard.press('F1')
  await expect(page.getByTestId('guide-view')).toBeVisible()
  await page.getByTestId('guide-card-people').click()
  await expect(page).toHaveURL(/\/guide\/people/)
  const concept = page.getByTestId('guide-concept')
  await expect(concept.getByTestId('guide-task-create-person')).toBeVisible()

  // The written page is one segment away.
  await page.getByTestId('guide-view-read').click()
  await expect(page.getByTestId('guide-read')).toContainText('Create a person')
  await page.getByTestId('guide-view-tasks').click()

  await concept.getByTestId('guide-show-create-person').click()
  const wizard = page.getByTestId('guide-wizard')
  await expect(wizard).toBeVisible()
  // The wizard leaves the Guide for the real screen.
  await expect(page.getByTestId('guide-view')).toBeHidden()
  await expect(wizard.getByTestId('guide-wizard-say')).toContainText('People')
  // Its ring sits over the sidebar's Add person control.
  const ring = page.locator('.gwz-ring')
  await expect(ring).toBeVisible()
  const target = await page.locator('[aria-label="Add person"]').first().boundingBox()
  const box = await ring.boundingBox()
  expect(Math.abs(box.x + box.width / 2 - (target.x + target.width / 2))).toBeLessThan(3)
  await page.keyboard.press('Escape')
  await expect(wizard).toBeHidden()
})

test('Crop and rotate takes any picture, rings the Edit tab, and Back returns to the grid', async ({
  page,
}) => {
  await openApp(page)
  await page.goto('/guide/edit-picture')
  await page.getByTestId('guide-show-crop-and-rotate').click()
  const wizard = page.getByTestId('guide-wizard')
  const say = wizard.getByTestId('guide-wizard-say')
  const ring = page.locator('.gwz-ring')
  await expect(say).toContainText('Double-click a picture')
  await expect(ring).toBeVisible()

  // Not the first picture: every picture on screen is inside the ring (the
  // step card covers part of the top row).
  await page.locator('.thumbnail-card').nth(9).dblclick()
  await expect(page).toHaveURL(/overlay=/)
  await expect(say).toContainText('Click the Edit tab')
  const tab = page.locator('.lightbox-sidebar [data-tab="edit"]')
  await expect(tab).toBeVisible()
  await expect
    .poll(async () => {
      const t = await tab.boundingBox()
      const r = await ring.boundingBox()
      return r && Math.abs(r.x + r.width / 2 - (t.x + t.width / 2)) < 3
    })
    .toBe(true)

  // Back is the grid again, not step 1 over an open lightbox.
  await wizard.getByRole('button', { name: 'Back' }).click()
  await expect(page).not.toHaveURL(/overlay=/)
  await expect(say).toContainText('Double-click a picture')
  await page.keyboard.press('Escape')
  await expect(wizard).toBeHidden()
})

test('the welcome shows once, and Not now records it as seen', async ({
  page,
  apiContext,
}) => {
  const reset = await apiContext.patch('/api/v1/users/me/config', {
    data: { guide_welcome_seen: '' },
  })
  expect(reset.ok()).toBe(true)
  try {
    await openApp(page)
    const welcome = page.getByTestId('guide-welcome')
    await expect(welcome).toBeVisible()
    await expect(page.locator('.app-dialog:has([data-testid="guide-welcome"])')).toContainText(
      '1 of 6',
    )
    await page.getByTestId('guide-welcome-not-now').click()
    await expect(welcome).toBeHidden()
    await expect
      .poll(async () => {
        const cfg = await (await apiContext.get('/api/v1/users/me/config')).json()
        return cfg.guide_welcome_seen
      })
      .toBe(WELCOME_EDITION)
    await page.reload()
    await expect(page.locator('.thumbnail-card').first()).toBeVisible({
      timeout: 15_000,
    })
    await expect(welcome).toBeHidden()
  } finally {
    await apiContext.patch('/api/v1/users/me/config', {
      data: { guide_welcome_seen: WELCOME_EDITION },
    })
  }
})

test('a step with nothing to ring moves on when its action opens the next control', async ({
  page,
}) => {
  await openApp(page)
  await page.goto('/guide/studio')
  await page.getByTestId('guide-show-locations').click()
  const say = page.getByTestId('guide-wizard-say')
  await expect(say).toContainText('Right-click the Sets heading')
  // The menu it opens holds step 2's control: that is the step being done.
  await page
    .locator('.sidebar-section-header')
    .filter({ hasText: 'Sets' })
    .first()
    .click({ button: 'right' })
  await expect(say).toContainText('Draw location sheets')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('guide-wizard')).toBeHidden()
})
