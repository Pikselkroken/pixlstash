import { test, expect } from '../fixtures/test.js'

// Release plan §3.4 / §11 — Stacks. The fixture has 10 stacks. Expanding one
// opens a tray under its card (design: "The Stack Opens a Tray").
//
// Driven in NON-compact mode: the deck edges and the tab's panel both need the
// padding compact mode removes, and they are two of the four things tying the
// tray to its card.
//
// "Only one stack is open at a time" is NOT asserted here, deliberately. The
// state is a single `expandedStackId`, so two trays are unrepresentable and any
// DOM count of them is a tautology that cannot fail. The rule lives in
// `toggleStackExpand` and is tested where it can be broken, in
// `src/composables/useStackOrdering.test.js`. Read-only.

/** The vertical part of an element's computed transform matrix. */
async function translateY(locator) {
  return locator.evaluate((el) => {
    const m = new DOMMatrixReadOnly(getComputedStyle(el).transform)
    return m.f
  })
}

test.describe('stacks', () => {
  test('a stack opens a tray tied to its own card', async ({
    page,
    grid,
  }) => {
    await grid.goto()
    // The suite shares one mutable backend and `preferences-persist.spec.js`
    // persists a compact-mode change, so the state on arrival is not knowable:
    // read it, do not toggle it blind. Restored at the end of the test.
    await grid.openViewMenu()
    const compactButton = page.locator('.tbm-btn--compact')
    const startedCompact = (await compactButton.getAttribute('class')).includes(
      'tbm-btn--on',
    )
    if (startedCompact) await compactButton.click()
    await page.keyboard.press('Escape')

    await expect(grid.stackBadges.first()).toBeVisible()
    await expect(grid.stackTray).toHaveCount(0)
    await expect(grid.stackTabCards).toHaveCount(0)

    // Collapsed, this stack's deck edges sit ABOVE its cover.
    const firstStackCard = page
      .locator('.image-card')
      .filter({ has: page.getByTestId('stack-badge') })
      .first()
    const tick = firstStackCard.locator('.stick--1')
    expect(await translateY(tick)).toBeLessThan(0)

    await grid.stackBadges.first().click()
    await expect(grid.stackTray).toBeVisible()
    await expect(grid.stackTrayHead).toContainText('Stack of')
    // Exactly one card is drawn as the tray's tab.
    await expect(grid.stackTabCards).toHaveCount(1)
    // Open, they turn over and point DOWN at the tray. This is one of the four
    // ties between the tray and its card, and the only one a unit test cannot
    // see (jsdom does not apply a component's scoped styles).
    const openTick = grid.stackTabCards.locator('.stick--1').first()
    expect(await translateY(openTick)).toBeGreaterThan(0)

    // The tray holds the stack's OTHER members: a stack of N draws N-1 tiles
    // in the panel, because the cover is the tab and is never drawn twice.
    const count = Number(
      (await grid.stackTrayHead.innerText()).match(/Stack of (\d+)/)[1],
    )
    expect(count).toBeGreaterThan(1)
    await expect(page.locator('.image-card--tray-member')).toHaveCount(
      count - 1,
    )

    // Clicking the open stack's own badge closes it again.
    await grid.stackTabCards.getByTestId('stack-badge').click()
    await expect(grid.stackTray).toHaveCount(0)
    await expect(grid.stackTabCards).toHaveCount(0)
    expect(await translateY(tick)).toBeLessThan(0)

    await grid.stackBadges.first().click()
    await expect(grid.stackTray).toBeVisible()
    await grid.openViewMenu()
    await grid.collapseStackButton.click()
    await expect(grid.stackTray).toHaveCount(0)
    await expect(grid.stackTabCards).toHaveCount(0)

    if (startedCompact) {
      await page.locator('.tbm-btn--compact').click()
    }
    await page.keyboard.press('Escape')
  })
})
