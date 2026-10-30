import { test, expect, readClientId } from '../fixtures/test.js'

// Undo and redo driven from the UI: the Ctrl+Z / Ctrl+Y chord
// (useGlobalKeydown.js, and the lightbox's own binding), the toolbar control
// (UndoControl.vue) and its History picker. Until this spec the only e2e undo
// was a direct `POST /operations/undo` in move-to-library.spec.js.
//
// Each change is made through the API carrying the page's own `X-Client-Id`,
// so the app treats it as this tab's action (receipt and all), and every
// verdict is read back from the server, never only from the grid. Every step
// is reversible, and each test puts the fixture back the way it found it.
//
// UndoControl is mounted by the Duplicates destination only: the grid lost its
// control in #2160 (Ctrl+Z and the receipt carry undo there), so the toolbar
// checks run on /duplicates.

const API = '/api/v1'
const CLIENT_HEADER = 'X-Client-Id'
const PROBE_TAG = 'e2e-undo-probe'

/** The pictures this spec may touch: the first few of the grid, live ones. */
async function livePictureIds(apiContext) {
  const res = await apiContext.get(`${API}/pictures`, {
    params: { fields: 'grid', limit: 12 },
  })
  expect(res.ok(), await res.text()).toBe(true)
  return (await res.json()).map((picture) => picture.id)
}

async function metadata(apiContext, pictureId) {
  const res = await apiContext.get(`${API}/pictures/${pictureId}/metadata`)
  expect(res.ok(), await res.text()).toBe(true)
  return res.json()
}

async function tagRows(apiContext, pictureId) {
  const res = await apiContext.get(`${API}/pictures/${pictureId}/tags`)
  expect(res.ok(), await res.text()).toBe(true)
  return (await res.json()).tags ?? []
}

async function tagLabels(apiContext, pictureId) {
  return (await tagRows(apiContext, pictureId)).map((row) => row.tag).sort()
}

/**
 * Open a page and wait for the tab's first read of the undo stack.
 *
 * That read sets the "already seen" watermark, and a change landing while it
 * is still in flight is folded into it: the change is undoable but never
 * narrated, so a test waiting on its receipt would wait forever.
 */
async function openWithHistory(page, open) {
  // Both halves: the store sets the watermark once the list AND the state
  // have answered.
  const firstReads = Promise.all([
    page.waitForResponse((res) => new URL(res.url()).pathname === `${API}/operations/undo-state`),
    page.waitForResponse((res) => new URL(res.url()).pathname === `${API}/operations`),
  ])
  await open()
  await firstReads
}

/** Every POST the page makes to the operation log, in the order it made them. */
function recordOperationPosts(page) {
  const posts = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && new URL(req.url()).pathname.startsWith(`${API}/operations`))
      posts.push(new URL(req.url()).pathname)
  })
  return posts
}

/** The id of the newest operation in the log: the one a test just made. */
async function newestOperationId(apiContext) {
  const res = await apiContext.get(`${API}/operations`, { params: { limit: 1 } })
  expect(res.ok(), await res.text()).toBe(true)
  return (await res.json())[0]?.id ?? null
}

/**
 * Cleanup: take back the test's own operation if it is still applied. By id,
 * never "the newest", so a test that failed before its change was logged
 * cannot revert somebody else's.
 */
async function undoIfApplied(apiContext, operationId) {
  if (operationId == null) return
  const res = await apiContext.get(`${API}/operations`, { params: { limit: 50 } })
  if (!res.ok()) {
    // Runs from `finally`: throwing here would replace the test's own failure.
    console.warn(
      `undo-redo cleanup: could not read the log (${res.status()}), operation ${operationId} may still be applied: ${await res.text()}`,
    )
    return
  }
  const op = (await res.json()).find((row) => row.id === operationId)
  if (op?.status !== 'applied') return
  const undone = await apiContext.post(`${API}/operations/${operationId}/undo`)
  expect(undone.ok(), await undone.text()).toBe(true)
}

/**
 * One change of each kind the issue names. `apply` makes it as the page's own
 * action; `read` is the server-side fact the change moves. A kind's `before`
 * is read live, so whatever earlier specs left on the picture is what undo has
 * to restore.
 */
const CHANGE_KINDS = [
  {
    name: 'a move to the Scrapheap',
    pick: (ids) => ids[0],
    read: async (api, id) => (await metadata(api, id)).deleted,
    apply: (api, id, headers) => api.delete(`${API}/pictures/${id}`, { headers }),
  },
  {
    name: 'adding a tag',
    pick: (ids) => ids[1],
    read: (api, id) => tagLabels(api, id),
    apply: (api, id, headers) =>
      api.post(`${API}/pictures/${id}/tags`, { headers, data: { tag: PROBE_TAG } }),
  },
  {
    name: 'removing a tag',
    pick: (ids) => ids[2],
    read: (api, id) => tagLabels(api, id),
    apply: async (api, id, headers) => {
      const [first] = await tagRows(api, id)
      expect(first, 'the fixture picture must carry a tag to remove').toBeTruthy()
      return api.delete(`${API}/pictures/${id}/tags/${first.id}`, { headers })
    },
  },
  {
    name: 'a rating',
    pick: (ids) => ids[3],
    read: async (api, id) => (await metadata(api, id)).score,
    apply: async (api, id, headers) => {
      // Any score but the current one, so the change is a change.
      const current = (await metadata(api, id)).score
      return api.post(`${API}/pictures/apply-scores`, {
        headers,
        data: { scores: { [String(id)]: current === 4 ? 2 : 4 }, only_unscored: false },
      })
    },
  },
  {
    name: 'adding to a set',
    pick: (ids) => ids[4],
    read: async (api, id, ctx) => {
      const res = await api.get(`${API}/picture_sets/${ctx.setId}/members`)
      expect(res.ok(), await res.text()).toBe(true)
      const body = await res.json()
      return (body.picture_ids ?? body).includes(id)
    },
    setup: async (api, ctx) => {
      const res = await api.post(`${API}/picture_sets`, {
        data: { name: `e2e-undo-set-${Date.now()}` },
      })
      expect(res.ok(), await res.text()).toBe(true)
      ctx.setId = (await res.json()).picture_set.id
    },
    teardown: async (api, ctx) => {
      if (ctx.setId != null) await api.delete(`${API}/picture_sets/${ctx.setId}`)
    },
    apply: (api, id, headers, ctx) =>
      api.post(`${API}/picture_sets/${ctx.setId}/members/${id}`, { headers }),
  },
  {
    name: 'assigning a character',
    // A face that belongs to one character, moved to another.
    pick: () => null,
    read: async (api, id) => {
      const res = await api.get(`${API}/pictures/${id}/faces`)
      expect(res.ok(), await res.text()).toBe(true)
      return (await res.json()).faces.map((face) => face.character_id)
    },
    setup: async (api, ctx, ids) => {
      const chars = await (await api.get(`${API}/characters`)).json()
      for (const id of ids) {
        const { faces } = await (await api.get(`${API}/pictures/${id}/faces`)).json()
        const face = faces.find((f) => f.character_id != null)
        const other = face && chars.find((c) => c.id !== face.character_id)
        if (other) {
          Object.assign(ctx, { pictureId: id, faceId: face.id, characterId: other.id })
          return
        }
      }
      throw new Error('no fixture picture has a face assigned to a character')
    },
    apply: (api, _id, headers, ctx) =>
      api.post(`${API}/characters/${ctx.characterId}/faces`, {
        headers,
        data: { face_ids: [ctx.faceId] },
      }),
  },
  {
    name: 'a rotation',
    pick: (ids) => ids[5],
    read: async (api, id) => (await metadata(api, id)).orientation ?? 1,
    apply: (api, id, headers) =>
      api.post(`${API}/pictures/rotate`, {
        headers,
        data: { picture_ids: [id], direction: 'cw' },
      }),
  },
]

test.describe('undo and redo from the keyboard', () => {
  for (const kind of CHANGE_KINDS) {
    test(`Ctrl+Z reverts ${kind.name} on the server, Ctrl+Y redoes it`, async ({
      page,
      grid,
      apiContext,
    }) => {
      const ids = await livePictureIds(apiContext)
      const ctx = {}
      await kind.setup?.(apiContext, ctx, ids)
      const pictureId = ctx.pictureId ?? kind.pick(ids)
      const read = () => kind.read(apiContext, pictureId, ctx)

      await openWithHistory(page, () => grid.goto())
      const headers = { [CLIENT_HEADER]: await readClientId(page) }
      const posts = recordOperationPosts(page)
      const receipt = page.locator('.receipt:not(.receipt--undone):not(.receipt-leave-active)')
      const undoneReceipt = page.locator('.receipt.receipt--undone')
      const before = await read()
      let operationId = null

      try {
        const res = await kind.apply(apiContext, pictureId, headers, ctx)
        expect(res.ok(), await res.text()).toBe(true)
        operationId = await newestOperationId(apiContext)
        await expect.poll(read).not.toEqual(before)
        const after = await read()
        // The receipt is this tab narrating its own change: the stack has been
        // re-read and the change is the step Ctrl+Z will take.
        await expect(receipt).toBeVisible()

        await page.keyboard.press('Control+z')
        await expect.poll(read).toEqual(before)
        await expect(undoneReceipt).toBeVisible()

        await page.keyboard.press('Control+y')
        await expect.poll(read).toEqual(after)
        await expect(receipt).toBeVisible()
        // One press, one step: a chord handled twice would take back an
        // earlier operation as well, and the state checks above cannot see it.
        expect(posts).toEqual([`${API}/operations/undo`, `${API}/operations/redo`])
      } finally {
        await undoIfApplied(apiContext, operationId)
        await kind.teardown?.(apiContext, ctx)
      }
    })
  }
})

test.describe('the toolbar undo control', () => {
  test('names the last operation, and its buttons undo and redo it', async ({
    page,
    duplicates,
    apiContext,
  }) => {
    const [pictureId] = await livePictureIds(apiContext)
    await openWithHistory(page, () => duplicates.goto())
    const headers = { [CLIENT_HEADER]: await readClientId(page) }
    const control = page.getByRole('group', { name: 'Undo and redo' })
    const undoButton = control.locator('.uc-btn--undo')
    const redoButton = control.locator('.uc-btn--redo')
    const posts = recordOperationPosts(page)

    const res = await apiContext.post(`${API}/pictures/${pictureId}/tags`, {
      headers,
      data: { tag: PROBE_TAG },
    })
    expect(res.ok(), await res.text()).toBe(true)
    const operationId = await newestOperationId(apiContext)
    try {
      const { next_undo: last } = await (
        await apiContext.get(`${API}/operations/undo-state`)
      ).json()
      expect(last.summary).toContain(PROBE_TAG)

      await expect(undoButton).toHaveAccessibleName(`Undo: ${last.summary} (Ctrl+Z)`)
      await expect(undoButton).toHaveAttribute('aria-disabled', 'false')
      await undoButton.click()
      await expect.poll(() => tagLabels(apiContext, pictureId)).not.toContain(PROBE_TAG)

      await expect(redoButton).toHaveAccessibleName(`Redo: ${last.summary} (Ctrl+Y)`)
      await redoButton.click()
      await expect.poll(() => tagLabels(apiContext, pictureId)).toContain(PROBE_TAG)
      expect(posts).toEqual([`${API}/operations/undo`, `${API}/operations/redo`])
    } finally {
      await undoIfApplied(apiContext, operationId)
    }
  })

  test('the History picker undoes back to a step, newest first, and no further', async ({
    page,
    duplicates,
    apiContext,
  }) => {
    const [pictureId] = await livePictureIds(apiContext)
    await openWithHistory(page, () => duplicates.goto())
    const headers = { [CLIENT_HEADER]: await readClientId(page) }
    const posts = recordOperationPosts(page)

    // Four steps. The picker is asked to go back to the second, so the first
    // must survive and the last three must go, newest first.
    const labels = ['keep', 'a', 'b', 'c'].map((s) => `e2e-undo-history-${s}`)
    for (const tag of labels) {
      const res = await apiContext.post(`${API}/pictures/${pictureId}/tags`, {
        headers,
        data: { tag },
      })
      expect(res.ok(), await res.text()).toBe(true)
    }
    const [opC, opB, opA] = await (
      await apiContext.get(`${API}/operations`, { params: { limit: 3 } })
    ).json()
    expect(opA.summary).toContain(labels[1])
    expect(opC.summary).toContain(labels[3])

    try {
      const control = page.getByRole('group', { name: 'Undo and redo' })
      await expect(control.locator('.uc-btn--undo')).toHaveAccessibleName(
        `Undo: ${opC.summary} (Ctrl+Z)`,
      )
      await control.locator('.uc-btn--chevron').click()
      const history = page.getByRole('dialog', { name: 'History' })
      await expect(history).toBeVisible()

      const stepA = history.getByRole('button', {
        name: `Undo back to: ${opA.summary} (3 steps)`,
      })
      // Pointing at a step previews exactly the range it would undo.
      await stepA.hover()
      await expect(history.locator('.uc-row--willundo')).toHaveCount(3)
      await stepA.click()

      await expect
        .poll(async () => (await tagLabels(apiContext, pictureId)).filter((t) => labels.includes(t)))
        .toEqual([labels[0]])
      expect(posts).toEqual([opC, opB, opA].map((op) => `${API}/operations/${op.id}/undo`))
    } finally {
      for (const row of await tagRows(apiContext, pictureId)) {
        if (row.tag.startsWith('e2e-undo-history-'))
          await apiContext.delete(`${API}/pictures/${pictureId}/tags/${row.id}`)
      }
    }
  })
})

test.describe('Ctrl+Z belongs to the field being typed in', () => {
  /**
   * Make a library change as this tab, type into `field`, press Ctrl+Z, and
   * prove the text was undone while the library change was not.
   */
  async function expectTextUndoOnly(page, apiContext, pictureId, field, posts) {
    await field.click()
    await page.keyboard.type('xyz')
    const typed = await field.inputValue()
    expect(typed).toContain('xyz')

    await page.keyboard.press('Control+z')
    await expect(field).not.toHaveValue(typed)
    // Long enough for a request the chord should never have made.
    await page.waitForTimeout(1_000)
    expect(posts).toEqual([])
    expect(await tagLabels(apiContext, pictureId)).toContain(PROBE_TAG)
  }

  async function addProbeTag(page, apiContext, pictureId) {
    const res = await apiContext.post(`${API}/pictures/${pictureId}/tags`, {
      headers: { [CLIENT_HEADER]: await readClientId(page) },
      data: { tag: PROBE_TAG },
    })
    expect(res.ok(), await res.text()).toBe(true)
  }

  async function removeProbeTag(apiContext, pictureId) {
    for (const row of await tagRows(apiContext, pictureId)) {
      if (row.tag === PROBE_TAG)
        await apiContext.delete(`${API}/pictures/${pictureId}/tags/${row.id}`)
    }
  }

  test('in the grid search field', async ({ page, grid, apiContext }) => {
    const [pictureId] = await livePictureIds(apiContext)
    await openWithHistory(page, () => grid.goto())
    const posts = recordOperationPosts(page)
    await addProbeTag(page, apiContext, pictureId)
    try {
      await expect(page.locator('.receipt')).toBeVisible()
      await grid.searchButton.click()
      await expect(grid.searchInput).toBeVisible()
      await expectTextUndoOnly(page, apiContext, pictureId, grid.searchInput, posts)
    } finally {
      await removeProbeTag(apiContext, pictureId)
    }
  })

  test('in the lightbox tag entry and description', async ({ page, lightbox, apiContext }) => {
    const [pictureId] = await livePictureIds(apiContext)
    await openWithHistory(page, () => page.goto(`/?overlay=${pictureId}`))
    await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
    await lightbox.openRail()
    const posts = recordOperationPosts(page)
    await addProbeTag(page, apiContext, pictureId)
    try {
      await expect(page.getByTestId('lightbox-action-receipt')).toBeVisible()
      await expectTextUndoOnly(page, apiContext, pictureId, lightbox.tagInput, posts)
      await lightbox.tagInput.fill('')

      const savedDescription = (await metadata(apiContext, pictureId)).description
      const description = page.locator('.lightbox .description-field textarea')
      await expectTextUndoOnly(page, apiContext, pictureId, description, posts)
      // Escape cancels the edit, so the typing never reaches the server.
      await page.keyboard.press('Escape')
      expect((await metadata(apiContext, pictureId)).description).toBe(savedDescription)
    } finally {
      await removeProbeTag(apiContext, pictureId)
    }
  })
})

test('Ctrl+Z in the lightbox undoes and keeps the lightbox open', async ({
  page,
  lightbox,
  apiContext,
}) => {
  const [pictureId] = await livePictureIds(apiContext)
  await openWithHistory(page, () => page.goto(`/?overlay=${pictureId}`))
  await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
  const receipt = page.locator('.lightbox-receipt:not(.lightbox-receipt--undone):not(.receipt-leave-active)')
  const posts = recordOperationPosts(page)

  const res = await apiContext.post(`${API}/pictures/${pictureId}/tags`, {
    headers: { [CLIENT_HEADER]: await readClientId(page) },
    data: { tag: PROBE_TAG },
  })
  expect(res.ok(), await res.text()).toBe(true)
  const operationId = await newestOperationId(apiContext)
  try {
    await expect(receipt).toBeVisible()
    await page.keyboard.press('Control+z')
    await expect.poll(() => tagLabels(apiContext, pictureId)).not.toContain(PROBE_TAG)
    await expect(page.locator('.lightbox-receipt.lightbox-receipt--undone')).toBeVisible()
    await expect(lightbox.root).toBeVisible()

    await page.keyboard.press('Control+y')
    await expect.poll(() => tagLabels(apiContext, pictureId)).toContain(PROBE_TAG)
    await expect(lightbox.root).toBeVisible()
    // The lightbox and the app-wide handler must not BOTH take the chord.
    expect(posts).toEqual([`${API}/operations/undo`, `${API}/operations/redo`])
  } finally {
    await undoIfApplied(apiContext, operationId)
  }
})

test('a read-only session: undo is inert, and the control says why', async ({
  browser,
  apiContext,
  baseURL,
}) => {
  const created = await apiContext.post(`${API}/users/me/token`, {
    data: { description: 'e2e undo read-only', scope: 'READ' },
  })
  expect(created.ok()).toBe(true)
  const { token } = await created.json()
  const context = await browser.newContext({ baseURL, storageState: undefined })
  const page = await context.newPage()
  const operationCalls = []
  page.on('request', (req) => {
    if (new URL(req.url()).pathname.startsWith(`${API}/operations`))
      operationCalls.push(`${req.method()} ${new URL(req.url()).pathname}`)
  })

  try {
    await page.goto(`/?token=${encodeURIComponent(token)}`)
    await expect(page.locator('.thumbnail-img').first()).toBeVisible({ timeout: 15_000 })
    // The chord is silent here (useGlobalKeydown declines read-only sessions
    // without a notice); what is asserted is that it calls nothing.
    await page.keyboard.press('Control+z')
    await page.keyboard.press('Control+y')

    await page.goto(`/duplicates?token=${encodeURIComponent(token)}`)
    const control = page.getByRole('group', { name: 'Undo and redo' })
    const undoButton = control.locator('.uc-btn--undo')
    await expect(undoButton).toHaveAccessibleName(
      'Undo is only available in your own library',
    )
    await expect(undoButton).toHaveAttribute('aria-disabled', 'true')
    await expect(control.locator('.uc-btn--redo')).toHaveAccessibleName(
      'Redo is only available in your own library',
    )
    await undoButton.click({ force: true })
    await page.keyboard.press('Control+z')

    await control.locator('.uc-btn--chevron').click()
    await expect(page.getByRole('dialog', { name: 'History' })).toContainText(
      'History is only available in your own library',
    )
    // Not one call to the owner-only log: not a read, not an undo.
    expect(operationCalls).toEqual([])
  } finally {
    await context.close()
  }
})

test('nothing to undo: the control is disabled and Ctrl+Z does nothing', async ({
  page,
  duplicates,
}) => {
  // The shared backend's log is never empty this late in a run, so the empty
  // stack is served to the page rather than made: what is under test is how
  // the UI answers it.
  const served = { state: 0, list: 0 }
  await page.route(`**${API}/operations/undo-state`, (route) => {
    served.state += 1
    return route.fulfill({
      json: { can_undo: false, can_redo: false, next_undo: null, next_redo: null },
    })
  })
  await page.route(`**${API}/operations?*`, (route) => {
    served.list += 1
    return route.fulfill({ json: [] })
  })
  // The real log is not empty: a regressed guard must not reach it.
  await page.route(`**${API}/operations/*do`, (route) =>
    route.fulfill({ status: 409, json: { detail: 'blocked by the e2e stub' } }),
  )
  const posts = recordOperationPosts(page)

  await openWithHistory(page, () => duplicates.goto())
  // The page is answering the served stack, not its own default.
  expect(served.state).toBeGreaterThan(0)
  expect(served.list).toBeGreaterThan(0)
  const control = page.getByRole('group', { name: 'Undo and redo' })
  const undoButton = control.locator('.uc-btn--undo')
  await expect(undoButton).toHaveAccessibleName('Nothing to undo (Ctrl+Z)')
  await expect(undoButton).toHaveAttribute('aria-disabled', 'true')
  await expect(control.locator('.uc-btn--redo')).toHaveAttribute('aria-disabled', 'true')

  await undoButton.click({ force: true })
  await page.keyboard.press('Control+z')
  await expect(page.locator('.notice-message', { hasText: 'Nothing to undo.' })).toBeVisible()
  await page.waitForTimeout(500)
  expect(posts).toEqual([])
})
