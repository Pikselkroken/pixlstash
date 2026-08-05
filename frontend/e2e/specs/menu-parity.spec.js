import { test, expect } from '../fixtures/test.js'

// Release plan §4 — Selection ▾ / context-menu parity, the manual check this
// spec exists to retire.
//
// SelectionBar.vue states the contract in prose: the Selection ▾ dropdown
// "mirrors the right-click context menu for every selection-scoped action, so
// keyboard ('S') and toolbar users reach the same actions as a right-click on
// the same selection", and names this file as the thing that asserts it.
//
// The two menus are SEPARATE components (SelectionMenu.vue and
// ImageGridContextMenu.vue) that mirror each other by convention, not by
// construction — SelectionMenu.vue even carries a "mirrors
// ImageGridContextMenu.vue" comment over its duplicated handlers. That is
// exactly how #403 happened: "Restore from snapshot" and "Reverse image
// search" were added to the context menu and not to the dropdown, and the gap
// was only caught by a human comparing them on release day. Nothing structural
// prevents a recurrence, so this runs on every PR.
//
// Scope is deliberately the MULTI-picture selection. Three context-menu items
// (Share image, Find similar faces, Remove all shares) are documented as
// intentionally context-only: they act on the specific right-clicked image and
// its per-image face/share state, which a selection-scoped dropdown has no
// single target for. They drop out of the context menu once the selection has
// more than one picture, which is what makes a strict comparison meaningful
// here and would make it wrong for a single selection.

/**
 * Sorted top-level action labels of a menu.
 *
 * Both menus share markup: `.ate-btn` for the Project / Character / Set entity
 * flyout triggers, `.ctx-item` for every other action. Two kinds of descendant
 * are excluded because they are the *contents* of an item rather than items:
 * submenu children (`.ctx-submenu`, e.g. per-plugin tag entries) and entity
 * flyout lists (`.ate-menu`).
 *
 * Icons render as font glyphs inside <i> elements, so innerText would prefix
 * every label with the glyph's private-use character. The icon elements are
 * stripped from a clone before reading text, which is what keeps the compared
 * labels equal to what a user would read aloud.
 */
function topLevelMenuLabels(rootLocator) {
  return rootLocator.evaluate((root) => {
    const label = (el) => {
      const clone = el.cloneNode(true)
      clone.querySelectorAll('i, .v-icon, svg').forEach((n) => n.remove())
      return (clone.textContent || '').replace(/\s+/g, ' ').trim()
    }
    const labels = []
    root.querySelectorAll('.ate-btn').forEach((btn) => {
      if (btn.closest('.ate-menu')) return
      const text = label(btn)
      if (text) labels.push(text)
    })
    root.querySelectorAll('.ctx-item').forEach((btn) => {
      if (btn.closest('.ctx-submenu') || btn.closest('.ate-menu')) return
      const text = label(btn)
      if (text) labels.push(text)
    })
    return labels.sort()
  })
}

// Documented as context-only in SelectionBar.vue: single-image actions with no
// selection-scoped equivalent. Listed here so that if one ever shows up for a
// multi-picture selection, this spec says which item and why it is surprising,
// instead of printing a bare diff.
const CONTEXT_ONLY_SINGLE_IMAGE_ACTIONS = [
  'Share image',
  'Find similar faces',
  'Remove all shares',
]

test.describe('selection / context menu parity (§4, #403)', () => {
  test('both menus list the same actions for a multi-picture selection', async ({
    grid,
  }) => {
    await grid.goto()

    const selected = await grid.selectCards(3)
    expect(
      selected,
      'fixture must render at least 2 cards to form a multi-selection',
    ).toBeGreaterThanOrEqual(2)

    await grid.openSelectionMenu()
    const selectionLabels = await topLevelMenuLabels(grid.selectionMenuPanel)
    await grid.closeSelectionMenu()

    // Guards against the whole spec passing vacuously if the panel renders
    // empty: two empty arrays compare equal.
    expect(
      selectionLabels.length,
      'Selection ▾ rendered no actions — the comparison below would be vacuous',
    ).toBeGreaterThan(0)

    await grid.openContextMenuOnSelected(0)
    const contextLabels = await topLevelMenuLabels(grid.contextMenu)
    await grid.closeContextMenu()

    // Fails loudly and specifically if a documented context-only action leaks
    // into a multi-picture context menu, rather than surfacing as an opaque
    // diff line in the parity assertion below.
    for (const action of CONTEXT_ONLY_SINGLE_IMAGE_ACTIONS) {
      expect(
        contextLabels,
        `"${action}" is documented as single-image only and must not appear ` +
          'for a multi-picture selection',
      ).not.toContain(action)
    }

    // toEqual prints the exact two-way diff when they diverge, which is what
    // made #403 diagnosable from CI output alone.
    expect(contextLabels).toEqual(selectionLabels)
  })
})
