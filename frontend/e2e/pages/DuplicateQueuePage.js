import { expect } from '@playwright/test'

/**
 * The Duplicates destination (DuplicateQueue.vue + DedupGroupRow.vue).
 *
 * The queue is a keyboard surface first: exactly one row is focused, and the
 * keys act on that row rather than on whatever the pointer last touched. This
 * object therefore exposes the focused row as a first-class locator, and every
 * key it sends goes to the queue root, which is the element that owns the
 * keydown handler.
 *
 * Selectors verified in source: `.dq` / `[data-testid="duplicate-queue"]` (the
 * root), `.grow` (a group row) with `.grow--focus` on the focused one and
 * `data-testid="dedup-group-<signature>"` carrying the group's server id,
 * `.gthumb` (a candidate) with `--cover` / `--out` modifiers, `.gbtn--stack`,
 * `.gcompare`, `.dc-dialog` fields inside Compare, and `.sidebar-list-item`
 * carrying "Duplicates" plus its `.sidebar-dedup-dot` badge.
 */
export class DuplicateQueuePage {
  constructor(page) {
    this.page = page
    this.root = page.getByTestId('duplicate-queue')
    this.rows = page.locator('.grow')
    this.focusedRow = page.locator('.grow--focus')
    this.doneState = page.locator('.qdone')
    this.header = page.locator('.qtitle')
    this.compareDialog = page.locator('.dc-strip')
    this.compareCards = page.locator('.dc-card')
    // The live region the queue narrates verdicts and refusals into. It is
    // outside the row branches on purpose, so it survives the verdict that
    // empties the queue.
    this.announcement = page.getByTestId('dedup-announcement')
    // The sidebar's Duplicates destination row and its PRESENCE dot: the
    // count was retired (it moved with the tier gate, so it read as churn) —
    // the dot only says there is work here.
    this.sidebarRow = page
      .locator('.sidebar-list-item', { hasText: 'Duplicates' })
      .first()
    this.sidebarDot = this.sidebarRow.locator('.sidebar-dedup-dot')
  }

  /**
   * Open the queue by route and wait for a group (or the done state).
   *
   * Waits for the row to be FOCUSED, not merely present. The queue sets its
   * focus index when a load resolves, so there is a window where rows are
   * painted and nothing is focused yet — and every key below acts on the
   * focused group, so a press landing in that window does nothing at all. That
   * window is narrow on a quiet machine and wide on a loaded CI runner, which
   * is exactly the shape of a test that passes locally and fails in CI.
   */
  async goto() {
    await this.page.goto('/duplicates')
    await expect(this.root).toBeVisible()
    await this.page
      .locator('.grow, .qdone')
      .first()
      .waitFor({ state: 'visible' })
    if (await this.doneState.isVisible()) return
    await expect(this.focusedRow).toHaveCount(1)
  }

  /**
   * Send a key to the queue root.
   *
   * Deliberately not `page.keyboard.press`: the handler is bound to `.dq`, and
   * a key pressed at the document with the focus elsewhere proves nothing about
   * the queue's model.
   */
  async pressKey(key) {
    await this.root.press(key)
  }

  /** The signature of the focused group, read off its test id. */
  async focusedSignature() {
    const id = await this.focusedRow.getAttribute('data-testid')
    return id ? id.replace('dedup-group-', '') : null
  }

  /** A row by group signature. */
  row(signature) {
    return this.page.getByTestId(`dedup-group-${signature}`)
  }

  /** The candidate thumbnails of the focused row. */
  focusedCandidates() {
    return this.focusedRow.locator('.gthumb')
  }

  /** How many candidates the focused row's Stack button would collect. */
  async focusedStackSize() {
    const text = await this.focusedRow.locator('.gbtn--stack').innerText()
    const match = text.match(/\d+/)
    return match ? Number(match[0]) : null
  }

}
