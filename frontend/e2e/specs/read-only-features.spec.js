import { test, expect } from '../fixtures/test.js'

// What a read-only (share-token) visitor SEES of the owner-only features.
//
// This is the demo site's contract: pixlstash.dev links to demo.pixlstash.dev
// with a whole-library READ token, and the demo's job is to demonstrate the
// product. The rule is show-but-disable — never hide a feature, never show data
// the token cannot have. Undo, Duplicates and the model shelf are the owner-only
// surfaces with a permanent home in the chrome, and all three were hidden or
// live-and-403ing until this spec covered them: the demo silently advertised a
// smaller product than PixlStash is.
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
      if (
        /\/api\/v1\/(operations|dedup|adapters|checkpoints|models|model-)/.test(
          url,
        )
      )
        forbidden.push(url)
    })

    // Every path the SPA has been on, including the history pushes vue-router
    // makes without a document load. An inert destination must never appear
    // here, even for the moment it would take a guard elsewhere to undo it.
    const visited = []
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) visited.push(new URL(frame.url()).pathname)
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

      // ── Models: the shelf is a third such destination (#1014) ──────────
      const models = page
        .locator('.sidebar-list-item', { hasText: 'Models' })
        .first()
      await expect(models).toBeVisible()
      await expect(models).toHaveAttribute('aria-disabled', 'true')
      await expect(models).toHaveAttribute(
        'title',
        'The model shelf is only available in your own library',
      )
      // Unlike the Duplicates row above, this one is a <button>, so
      // `aria-disabled` reaches the accessibility tree as a real disabled
      // state — which is the point of using a button, and which a screen
      // reader announces. Asserted rather than assumed.
      await expect(models).toBeDisabled()
      // …but it stays tabbable, so a keyboard user can land on it and hear the
      // explanation. That is why `aria-disabled` and not the native attribute.
      await expect(models).not.toHaveAttribute('disabled', /.*/)

      // `force`, because Playwright refuses to click a control the a11y tree
      // reports as disabled — correctly, and that refusal is asserted above.
      // Forcing it through is what proves the second guard: the handler itself
      // declines, so even an activation that got past the disabled state
      // navigates nowhere.
      await models.click({ force: true })
      await page.waitForTimeout(500)
      // Asserted on where the page WENT, not on where it ended up. The shelf
      // route is guarded twice for this session — the row refuses to emit, and
      // `useAppNavigation` bounces the route if anything else reaches it — so a
      // final-URL check passes with the row's guard deleted and only tests the
      // bounce. `visited` sees the push that a broken row would make, before the
      // bounce undoes it.
      expect(visited).not.toContain('/models')
      expect(new URL(page.url()).pathname).not.toContain('models')

      // ── None of the three woke the owner-only API ──────────────────────
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

  // Models diverges from Duplicates here on purpose, and only here. The shelf
  // is the one destination whose whole body is the host machine's filesystem,
  // so there is no partial view of it to render: mounting it would be a screen
  // of empty state above a live toolbar of owner-only verbs. It bounces
  // instead, and the destination explains itself where the visitor actually
  // meets it — the sidebar row, above.
  test('bounces the model shelf when it is reached by URL', async ({
    browser,
    apiContext,
    baseURL,
  }) => {
    const token = await mintLibraryReadToken(apiContext)
    const context = await browser.newContext({ baseURL, storageState: undefined })
    const page = await context.newPage()

    const shelfCalls = []
    page.on('request', (req) => {
      const path = new URL(req.url()).pathname
      if (/^\/api\/v1\/(adapters|checkpoints|models|model-)/.test(path))
        shelfCalls.push(path)
    })

    try {
      await page.goto(`/models?token=${encodeURIComponent(token)}`)

      // Landed somewhere it may actually use. Waiting on the navigation rather
      // than on the sidebar: the bounce is queued in `App.vue`'s setup, so the
      // rail paints a tick before the URL settles and sampling `page.url()` off
      // the render reads the route the visitor is being moved OFF.
      await page.waitForURL((u) => new URL(u).pathname === '/', {
        timeout: 15_000,
      })
      await expect(page.locator('.sidebar-list-item').first()).toBeVisible()
      const url = new URL(page.url())
      // …still holding its credential. The bounce is the only navigation that
      // fires exclusively for a share session, so dropping `?token=` here would
      // break the link on the visitor's next reload and nobody else's.
      expect(url.searchParams.get('token')).toBe(token)
      // …and asked the shelf for nothing on the way through.
      expect(shelfCalls).toEqual([])
    } finally {
      await context.close()
    }
  })
})
