import { test, expect } from '../fixtures/test.js'

// What a read-only (share-token) visitor SEES of the owner-only features.
//
// This is the demo site's contract: pixlstash.dev links to demo.pixlstash.dev
// with a whole-library READ token, and the demo's job is to demonstrate the
// product. The rule is show-but-disable — never hide a feature, never show data
// the token cannot have. Undo and Duplicates are the two owner-only surfaces
// with a permanent home in the chrome, and both were hidden outright until this
// spec: the demo silently advertised a smaller product than PixlStash is.
//
// Driven end to end because the interesting half is the backend: /operations*
// and /dedup/* are OWNER_ONLY, so the affordances must be inert AND quiet. A
// visible-but-live control would 403 on every read.

/** Mint the demo's token shape: READ over the whole library, no resource. */
async function mintLibraryReadToken(apiContext) {
  const created = await apiContext.post('/api/v1/users/me/token', {
    data: { description: 'e2e read-only features', scope: 'READ' },
  })
  expect(created.ok()).toBe(true)
  const { token } = await created.json()
  return token
}

test.describe('read-only session: owner-only features stay visible', () => {
  test('shows Undo and Duplicates as inert rather than hiding them', async ({
    browser,
    apiContext,
    baseURL,
  }) => {
    const token = await mintLibraryReadToken(apiContext)
    const context = await browser.newContext({ baseURL, storageState: undefined })
    const page = await context.newPage()

    // Any owner-only read reaching the server would be a 403 behind a control
    // that looks alive. Collected for the assertion at the end.
    const forbidden = []
    page.on('response', (res) => {
      const url = res.url()
      if (res.status() !== 403) return
      if (/\/api\/v1\/(operations|dedup)\b/.test(url)) forbidden.push(url)
    })

    try {
      await page.goto(`/?token=${encodeURIComponent(token)}`)

      // ── Undo: mounted, inert, and it says why ──────────────────────────
      const undo = page.locator('.uc-btn--undo')
      await expect(undo).toBeVisible({ timeout: 15_000 })
      await expect(undo).toHaveAttribute('aria-disabled', 'true')
      await expect(undo).toHaveAttribute(
        'title',
        'Undo is only available in your own library',
      )
      // `aria-disabled`, not the native attribute: the control stays tabbable
      // so a keyboard user reaches the explanation too.
      await expect(undo).not.toHaveAttribute('disabled', /.*/)

      // ── Duplicates: a visible destination that does not navigate ───────
      const duplicates = page
        .locator('.sidebar-list-item', { hasText: 'Duplicates' })
        .first()
      await expect(duplicates).toBeVisible()
      await expect(duplicates).toHaveAttribute('aria-disabled', 'true')
      await expect(duplicates).toHaveAttribute(
        'title',
        'Duplicate review is only available in your own library',
      )
      // No badge: the dot reports a fact about the library that this session
      // cannot read, so it must never appear.
      await expect(duplicates.locator('.sidebar-dedup-dot')).toHaveCount(0)

      await duplicates.click()
      await page.waitForTimeout(500)
      expect(new URL(page.url()).pathname).not.toContain('duplicates')

      // ── Neither control woke the owner-only API ────────────────────────
      expect(forbidden).toEqual([])
    } finally {
      await context.close()
    }
  })

  test('explains the Duplicates destination when it is reached by URL', async ({
    browser,
    apiContext,
    baseURL,
  }) => {
    const token = await mintLibraryReadToken(apiContext)
    const context = await browser.newContext({ baseURL, storageState: undefined })
    const page = await context.newPage()

    try {
      await page.goto(`/duplicates?token=${encodeURIComponent(token)}`)

      const state = page.locator('[data-testid="dedup-read-only"]')
      await expect(state).toBeVisible({ timeout: 15_000 })
      await expect(state).toContainText('only available in your own library')
      // Never "Queue clear": that asserts a library-wide fact this session has
      // no way to know, and "Confirming whether the queue is clear" would spin
      // forever, since the counts it waits on are owner-only.
      await expect(page.locator('.qdone h3')).toHaveText('Duplicate review')
      await expect(page.locator('.dq-state')).toHaveCount(0)
    } finally {
      await context.close()
    }
  })
})
