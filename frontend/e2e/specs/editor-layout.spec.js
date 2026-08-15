import { test, expect } from '../fixtures/test.js'

// The person and picture-set editors are two-column so they stop outgrowing the
// viewport and scrolling `AppDialog`'s body. That is the whole point of the
// layout and it is not something a jsdom unit test can see: the branch which
// PICKS the layout is covered in
// `src/components/editors/EditorTwoColumnLayout.test.js`, and this file covers
// the thing the layout exists to achieve, in a browser with real box metrics.
//
// The assertion is `scrollHeight === clientHeight` on `.app-dialog__body` — the
// same shape as `overlay-tag-list-scroll.spec.js`. Navigation and dialog-open
// only; nothing here mutates the fixture library.

const VIEWPORT = { width: 1280, height: 800 }

/** Right-click a sidebar row and choose Edit. */
async function openEditorFor(sidebar, row) {
  await expect(row).toBeVisible()
  await row.click({ button: 'right' })
  await expect(sidebar.ctxMenu).toBeVisible()
  // The page object's exact-name helper, not a substring match: the character
  // menu also carries `Share "<name>"`, so a loose "Edit" would match that row
  // instead for any person whose name happens to contain those four letters.
  await sidebar.ctxButton('Edit').click()
}

/** Metrics of the open dialog, once its enter transition has settled. */
async function dialogMetrics(page, { waitFor = [] } = {}) {
  const dialog = page.locator('.app-dialog')
  await expect(dialog).toBeVisible()
  await expect(page.locator('.editor-body')).toBeVisible()
  // Two different waits, for two different things. Every block the height
  // measurement depends on is POLLED, because each is gated on its own fetch —
  // the reference grid is two sequential round trips deep and the adapter tray
  // one — and a sleep long enough for those on a loaded CI runner is a sleep
  // that makes this spec slow for everyone. Miss one and `scrollHeight` is read
  // with a row still missing, which is how a "does not scroll" assertion goes
  // green on a layout that scrolls.
  for (const sel of waitFor) {
    await expect(page.locator(sel).first()).toBeVisible()
  }
  // The fixed wait is only for the dialog's own enter/leave transition, which
  // is time-based rather than content-based and so has nothing to poll on.
  await page.waitForTimeout(300)
  return dialog.evaluate((el) => {
    const body = el.querySelector('.app-dialog__body')
    const cols = [...el.querySelectorAll('.editor-col')]
    return {
      bodyClient: body.clientHeight,
      bodyScroll: body.scrollHeight,
      columns: cols.length,
      colTops: cols.map((c) => Math.round(c.getBoundingClientRect().top)),
      // The dialog body, NOT the document: Vuetify blocks scrolling on <html>
      // while a dialog is open, so the document's own overflow stays 0 however
      // far the layout blows out sideways and cannot fail.
      bodyOverflowX: body.scrollWidth - body.clientWidth,
      referenceThumbs: el.querySelectorAll('.ref-picture-thumb').length,
      // Track WIDTH, not count: `repeat(8, 1fr)` still reports eight tracks
      // when the row is squeezed into one column — they just get too narrow
      // for the 32px buttons sitting in them.
      iconTrack: (() => {
        const g = el.querySelector('.icon-grid')
        if (!g) return null
        return Math.round(parseFloat(getComputedStyle(g).gridTemplateColumns))
      })(),
      colWidth: cols.length ? Math.round(cols[0].getBoundingClientRect().width) : null,
      appearanceWidth: (() => {
        const a = el.querySelector('.appearance-row')
        return a ? Math.round(a.getBoundingClientRect().width) : null
      })(),
      trayWidth: (() => {
        const t = el.querySelector('.adapter-tray')
        return t ? Math.round(t.getBoundingClientRect().width) : null
      })(),
      dialogWidth: Math.round(el.getBoundingClientRect().width),
    }
  })
}

test.describe('editor dialogs fit without scrolling', () => {
  test.beforeEach(async ({ page, grid }) => {
    await page.setViewportSize(VIEWPORT)
    await grid.goto()
  })

  test('the person editor is two columns and its body does not scroll', async ({
    page,
    sidebar,
  }) => {
    await openEditorFor(sidebar, await sidebar.firstNonEmpty(sidebar.characterItems))
    const m = await dialogMetrics(page, {
      waitFor: ['.ref-picture-thumb', '.adapter-tray'],
    })

    expect(m.columns).toBe(2)
    // Side by side, not stacked — a collapsed grid would also "not scroll" if
    // the content happened to be short.
    expect(m.colTops[0]).toBe(m.colTops[1])
    expect(m.bodyScroll).toBe(m.bodyClient)
    // The right column has to be carrying something, or "it fits" is a claim
    // about an empty box rather than about the layout.
    expect(m.referenceThumbs).toBeGreaterThan(0)
    expect(m.dialogWidth).toBe(720)
    // The tray spans both columns rather than sitting in one. Note this
    // measures the row's WIDTH, not a populated tray's height: the fixture
    // library attaches no adapters, and a library with some puts cards in this
    // row three across instead of one, eating into the headroom measured above.
    // Deliberately not asserted — the adapter count is a property of the shared
    // fixture, so pinning it would fail this spec from an unrelated change.
    expect(m.trayWidth).toBeGreaterThan(m.colWidth * 1.8)
  })

  test('the picture-set editor is two columns and its body does not scroll', async ({
    page,
    sidebar,
  }) => {
    await openEditorFor(sidebar, await sidebar.firstNonEmpty(sidebar.setItems))
    const m = await dialogMetrics(page, { waitFor: ['.icon-btn', '.adapter-tray'] })

    expect(m.columns).toBe(2)
    expect(m.colTops[0]).toBe(m.colTops[1])
    expect(m.bodyScroll).toBe(m.bodyClient)
    // The appearance row spans rather than sitting in a column. Squeezed into
    // half the dialog it would keep the body from scrolling anyway — the icon
    // grid has its own scroller — so the metrics above cannot see this alone.
    expect(m.appearanceWidth).toBeGreaterThan(m.colWidth * 1.8)
    // And its eight icon tracks stay wide enough for the 32px buttons in them.
    expect(m.iconTrack).toBeGreaterThanOrEqual(32)
    expect(m.trayWidth).toBeGreaterThan(m.colWidth * 1.8)
    expect(m.dialogWidth).toBe(720)
  })

  test('a narrow window collapses to one column instead of overflowing', async ({
    page,
    sidebar,
  }) => {
    await openEditorFor(sidebar, await sidebar.firstNonEmpty(sidebar.characterItems))
    await expect(page.locator('.app-dialog')).toBeVisible()
    // Resize with the dialog open: below 720 the media query stacks the
    // columns, which is the layout the editors had before this one.
    await page.setViewportSize({ width: 700, height: 900 })
    const m = await dialogMetrics(page, { waitFor: ['.adapter-tray'] })

    expect(m.colTops[0]).toBeLessThan(m.colTops[1])
    expect(m.bodyOverflowX).toBe(0)
  })
})
