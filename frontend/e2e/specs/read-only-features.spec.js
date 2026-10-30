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
  test('shows Duplicates and Models as inert rather than hiding them', async ({
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

      // Undo has no control in the grid since #2160 (Ctrl+Z and the receipt
      // carry it), so the inert-but-visible case starts at Duplicates.
      // ── Duplicates: a visible destination that does not navigate ───────
      const duplicates = page
        .locator('.sidebar-list-item', { hasText: 'Duplicates' })
        .first()
      await expect(duplicates).toBeVisible({ timeout: 15_000 })
      await expect(duplicates).toHaveAttribute('aria-disabled', 'true')
      // A labelled row keeps its name; the reason is its tooltip, exposed to
      // assistive tech as the row's accessible description (aria-describedby,
      // which every engine exposes, not the Chromium-only aria-description).
      await expect(duplicates).toHaveAccessibleDescription(
        'Duplicate review is only available in your own library',
      )
      // No badge: the dot reports a fact about the library that this session
      // cannot read, so it must never appear.
      await expect(duplicates.locator('.sidebar-dedup-dot')).toHaveCount(0)

      // `force`, because the row is a <button aria-disabled="true"> now (it was
      // a div until #837 made the sidebar destinations keyboard-operable).
      // Playwright's actionability check reads aria-disabled on button-role
      // elements as disabled and would wait out the timeout rather than click —
      // which is the right reading, and exactly what this test needs to bypass:
      // the point is that a user CAN still press it and nothing happens.
      await duplicates.click({ force: true })
      await page.waitForTimeout(500)
      expect(new URL(page.url()).pathname).not.toContain('duplicates')

      // ── Models: the shelf is a third such destination (#1014) ──────────
      // A workspace on the title bar's switch now (#2158), not a sidebar row.
      const models = page
        .getByRole('navigation', { name: 'Workspace' })
        .getByRole('button', { name: 'Models' })
      await expect(models).toBeVisible()
      await expect(models).toHaveAttribute('aria-disabled', 'true')
      await expect(models).toHaveAccessibleDescription(
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

  // The workflow library is the Models case again (#2319): every /workflows
  // route is OWNER_ONLY, so the whole screen is the owner-only action. Its
  // verbs (delete, rename, Save, Run, download missing) are never drawn for a
  // READ session, because the screen that holds them is never mounted; what
  // stays visible and explains itself is the workspace segment.
  test('shows Workflows as inert, and the owner session still reaches it', async ({
    browser,
    apiContext,
    baseURL,
    page: ownerPage,
  }) => {
    // The positive control first: the owner's own session goes there, so the
    // refusal below is the token's and not a broken route.
    await ownerPage.goto('/')
    const ownerWorkflows = ownerPage
      .getByRole('navigation', { name: 'Workspace' })
      .getByRole('link', { name: 'Workflows' })
    await ownerWorkflows.click()
    await ownerPage.waitForURL((u) => new URL(u).pathname === '/workflows')
    await expect(ownerPage.getByRole('region', { name: 'Workflows' })).toBeVisible()

    const token = await mintLibraryReadToken(apiContext)
    const context = await browser.newContext({ baseURL, storageState: undefined })
    const page = await context.newPage()

    const workflowCalls = []
    page.on('request', (req) => {
      const path = new URL(req.url()).pathname
      if (path.startsWith('/api/v1/workflows')) workflowCalls.push(path)
    })
    const visited = []
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) visited.push(new URL(frame.url()).pathname)
    })

    try {
      await page.goto(`/?token=${encodeURIComponent(token)}`)
      // What a READ session can do still works: the library's pictures.
      await expect(page.locator('.thumbnail-card').first()).toBeVisible({
        timeout: 15_000,
      })

      const workspaces = page.getByRole('navigation', { name: 'Workspace' })
      const workflows = workspaces.getByRole('button', { name: 'Workflows' })
      await expect(workflows).toBeVisible()
      await expect(workflows).toHaveAttribute('aria-disabled', 'true')
      await expect(workflows).toHaveAccessibleDescription(
        'The workflow library is only available in your own library',
      )
      await expect(workflows).toBeDisabled()
      await expect(workflows).not.toHaveAttribute('disabled', /.*/)

      // `force` for the reason given for Models above: the press must reach
      // the segment, and navigate nowhere.
      await workflows.click({ force: true })
      await page.waitForTimeout(500)
      expect(visited).not.toContain('/workflows')
      // The open segment beside it is a positive control in the same bar.
      await expect(workspaces.getByRole('link', { name: 'Media' })).toBeVisible()
      expect(workflowCalls).toEqual([])
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

  // A pasted workflow link, deep link and all, bounces the same way: the
  // selection, the rail and Edit LoRAs it asks for are all owner-only reads.
  test('bounces a Workflows deep link when it is reached by URL', async ({
    browser,
    apiContext,
    baseURL,
  }) => {
    const token = await mintLibraryReadToken(apiContext)
    const context = await browser.newContext({ baseURL, storageState: undefined })
    const page = await context.newPage()

    const workflowCalls = []
    page.on('request', (req) => {
      const path = new URL(req.url()).pathname
      if (path.startsWith('/api/v1/workflows')) workflowCalls.push(path)
    })

    try {
      const link = new URLSearchParams({
        workflow: `auto:${'a'.repeat(64)}`,
        edit: 'loras',
        token,
      })
      await page.goto(`/workflows?${link}`)

      await page.waitForURL((u) => new URL(u).pathname === '/', {
        timeout: 15_000,
      })
      await expect(page.locator('.thumbnail-card').first()).toBeVisible()
      expect(new URL(page.url()).searchParams.get('token')).toBe(token)
      await expect(page.getByRole('region', { name: 'Workflows' })).toHaveCount(0)
      await expect(page.getByRole('dialog', { name: 'Edit LoRAs' })).toHaveCount(0)
      expect(workflowCalls).toEqual([])
    } finally {
      await context.close()
    }
  })
})
