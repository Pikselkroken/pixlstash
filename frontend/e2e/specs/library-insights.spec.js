// "About your library" (/insights, LibraryInsights.vue) on the e2e fixture
// (#2325).
//
// The fixture is a vault-managed library (plus the seeded reference folder's
// few pictures), every picture captioned and tagged, with faces nobody has
// named. So it produces one finding to act on (unnamed faces) and clear ones
// for the rest, and those are asserted as the real backend renders them. The other action kinds (a Settings pane, the
// Duplicates queue scoped to a folder or unscoped, Unassigned narrowed to a
// folder) need a library this fixture is not, so their buttons are driven from
// a stubbed /insights payload: what is under test there is that each button
// lands where its label says, which is the frontend's half of the contract.

import { test, expect } from '../fixtures/test.js'

/** A finding in the backend's shape (library_insights_service._finding). */
function finding(id, action) {
  return {
    id,
    state: 'todo',
    title: `stub ${id}`,
    evidence: `stub evidence for ${id}`,
    action: { note: null, path: null, folder_label: null, tab: null, ...action },
  }
}

const FOLDER = '/home/me/Pictures/Shoots'

const STUB = {
  total_pictures: 10,
  folder_pictures: 10,
  folders: 2,
  findings: [
    finding('unsorted_pile', {
      label: 'Sort the pile',
      kind: 'unassigned_in_folder',
      path: FOLDER,
    }),
    finding('overlapping_folders', {
      label: 'Review the overlap',
      kind: 'duplicates_in_folder',
      path: FOLDER,
      folder_label: 'Shoots',
    }),
    finding('overlapping_folders_wide', {
      label: 'Review all duplicates',
      kind: 'duplicates',
    }),
    finding('uncaptioned', {
      label: 'Choose a captioner',
      kind: 'settings',
      tab: 'behaviour',
    }),
  ],
}

/** The finding row for one id, and its action button. */
function row(page, id) {
  return page.locator(`.ins-find[data-finding="${id}"]`)
}

test.describe('About your library on the fixture', () => {
  test('renders the fixture findings, the one to act on first', async ({ page, apiContext }) => {
    const res = await apiContext.get('/api/v1/insights')
    expect(res.ok(), await res.text()).toBe(true)
    const data = await res.json()

    await page.goto('/insights')
    const view = page.getByTestId('library-insights')
    await expect(view.getByRole('heading', { name: 'About your library' })).toBeVisible()

    // Every finding the backend returned is a row, in the backend's order.
    const rows = view.locator('.ins-find')
    await expect(rows).toHaveCount(data.findings.length)
    expect(await rows.evaluateAll((els) => els.map((el) => el.dataset.finding))).toEqual(
      data.findings.map((f) => f.id),
    )

    // The fixture's one finding to act on, and the clear ones after it.
    const faces = data.findings.find((f) => f.id === 'unnamed_faces')
    expect(faces?.state, JSON.stringify(data.findings)).toBe('todo')
    await expect(rows.first()).toHaveAttribute('data-finding', 'unnamed_faces')
    await expect(row(page, 'unnamed_faces')).toContainText(faces.title)
    await expect(
      row(page, 'unnamed_faces').getByRole('button', { name: 'Name the faces' }),
    ).toBeVisible()
    // A clear check says so and offers nothing to click. The fixture is
    // captioned and tagged throughout, so at least those two are clear.
    const clear = data.findings.filter((f) => f.state === 'clear').map((f) => f.id)
    expect(clear).toEqual(expect.arrayContaining(['uncaptioned', 'untagged']))
    for (const id of clear) {
      await expect(row(page, id)).toHaveAttribute('data-state', 'clear')
      await expect(row(page, id)).toContainText('nothing to do')
      await expect(row(page, id).getByRole('button')).toHaveCount(0)
    }

    // The lede counts what the backend counted, and the scope line says how
    // little of the library sits in folders: the seeded reference folder's
    // few pictures, against a vault-managed fixture.
    await expect(view.locator('.ins-lede')).toContainText(
      `${data.findings.length} things worth knowing about the ${data.total_pictures} pictures`,
    )
    expect(data.folder_pictures).toBeGreaterThan(0)
    expect(data.folder_pictures).toBeLessThan(data.total_pictures)
    await expect(view.locator('.ins-scope')).toContainText(
      `${data.folder_pictures} of them sit in ${data.folders} folders PixlStash reads in place`,
    )
  })

  test('"Name the faces" opens Unassigned narrowed to pictures with a face', async ({
    page,
  }) => {
    await page.goto('/insights')
    await row(page, 'unnamed_faces').getByRole('button', { name: 'Name the faces' }).click()
    await expect(page).toHaveURL(/\/character\/UNASSIGNED\?face=with_face$/)
    await expect(page.getByTestId('library-insights')).toHaveCount(0)
    await expect(page.locator('.thumbnail-card').first()).toBeVisible()
  })

  test('nothing in the library changes while the screen is read and acted on', async ({
    page,
    apiContext,
  }) => {
    const before = await (await apiContext.get('/api/v1/insights')).json()

    await page.goto('/insights')
    await expect(row(page, 'unnamed_faces')).toBeVisible()
    // Counted from here: booting the app shell around the screen persists the
    // owner's view preferences and asks for the sidebar's duplicate counts,
    // neither of which is this screen's doing.
    await page.waitForLoadState('networkidle')
    const writes = []
    page.on('request', (req) => {
      const url = new URL(req.url())
      if (req.method() !== 'GET' && url.pathname.startsWith('/api/')) {
        writes.push(`${req.method()} ${url.pathname}`)
      }
    })
    const reread = page.waitForResponse('**/api/v1/insights')
    await page.getByRole('button', { name: 'Look again' }).click()
    await reread
    await expect(row(page, 'unnamed_faces')).toBeVisible()
    expect(writes).toEqual([])

    await row(page, 'unnamed_faces').getByRole('button', { name: 'Name the faces' }).click()
    await expect(page).toHaveURL(/\/character\/UNASSIGNED/)
    await expect(page.locator('.thumbnail-card').first()).toBeVisible()
    const after = await (await apiContext.get('/api/v1/insights')).json()
    expect(after).toEqual(before)
  })
})

test.describe('About your library: every kind of action button', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/v1/insights', (route) =>
      route.fulfill({ json: STUB }),
    )
    await page.goto('/insights')
    await expect(page.locator('.ins-find')).toHaveCount(STUB.findings.length)
  })

  test('a folder\'s unsorted pile opens Unassigned on that folder', async ({ page }) => {
    await row(page, 'unsorted_pile').getByRole('button', { name: 'Sort the pile' }).click()
    await expect(page).toHaveURL(/\/character\/UNASSIGNED\?/)
    const url = new URL(page.url())
    expect(url.searchParams.get('path')).toBe(FOLDER)
    expect(url.searchParams.has('face')).toBe(false)
  })

  test('an overlap inside one folder opens Duplicates scoped to it', async ({ page }) => {
    await row(page, 'overlapping_folders')
      .getByRole('button', { name: 'Review the overlap' })
      .click()
    await expect(page.getByTestId('duplicate-queue')).toBeVisible()
    const url = new URL(page.url())
    expect(url.pathname).toBe('/duplicates')
    expect(url.searchParams.get('scope')).toBe('folder')
    expect(url.searchParams.get('scope_id')).toBe(FOLDER)
    expect(url.searchParams.get('scope_label')).toBe('Shoots')
  })

  test('an overlap with no common folder opens the whole Duplicates queue', async ({
    page,
  }) => {
    await row(page, 'overlapping_folders_wide')
      .getByRole('button', { name: 'Review all duplicates' })
      .click()
    await expect(page.getByTestId('duplicate-queue')).toBeVisible()
    const url = new URL(page.url())
    expect(url.pathname).toBe('/duplicates')
    expect(url.search).toBe('')
  })

  test('a Settings action opens the pane it names and stays on the screen', async ({
    page,
    settings,
  }) => {
    await row(page, 'uncaptioned').getByRole('button', { name: 'Choose a captioner' }).click()
    await expect(settings.card).toBeVisible()
    await expect(
      settings.card.locator('.settings-nav-item', { hasText: 'Tagging' }),
    ).toHaveAttribute('aria-current', 'page')
    await expect(settings.card.locator('#settings-pane-behaviour')).toBeVisible()
    // A dialog, not a navigation: the insights screen is still behind it.
    await expect(page).toHaveURL(/\/insights$/)
  })
})
