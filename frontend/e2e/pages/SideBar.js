import { expect } from '@playwright/test'

/**
 * The left sidebar (SideBar.vue) — navigation that filters the grid by picture
 * set (§7), project (§8), and character/person (§9). Thin wrapper around the
 * stable selectors verified in source: .sidebar-set-item, the character rows in
 * .sidebar-character-group, and the Library tree's project rows (#2448), each
 * of which opens in place to hold its people and sets. Per-row counts live in
 * .sidebar-list-count.
 */
export class SideBar {
  constructor(page) {
    this.page = page
    this.setItems = page.locator('.sidebar-set-item')
    this.characterItems = page.locator(
      '.sidebar-character-group > .sidebar-list-item:not(.sidebar-set-item)',
    )
    // The Library tree's project rows and its Not in a project row (#2448).
    // Choosing one is "all of that project"; it lights with aria-current.
    this.projectRows = page.getByTestId('sidebar-project-row')
    this.noProjectRow = page.getByTestId('sidebar-no-project-row')
    this.allMedia = page
      .locator('.sidebar-media-list .sidebar-list-item')
      .filter({ hasText: /^All media/ })
    this.find = page.getByTestId('sidebar-find')
    // Right-click context menu (teleported to <body>) and the trailing lock
    // indicator that a set row renders when the set is locked (SideBar.vue:
    // .sidebar-lock-icon on a .sidebar-set-item).
    this.ctxMenu = page.locator('.sidebar-ctx-menu')
    this.setLockIcons = page.locator('.sidebar-set-item .sidebar-lock-icon')
  }

  /** The count badge inside a set/character/project row. */
  count(row) {
    return row.locator('.sidebar-list-count').first()
  }

  /**
   * The first row whose count badge is a positive number — picks a non-empty
   * entity so a "grid filters to it" assertion has thumbnails to show. Falls
   * back to the first row when no count is readable.
   */
  async firstNonEmpty(items) {
    const n = await items.count()
    for (let i = 0; i < n; i++) {
      const row = items.nth(i)
      const txt = (await this.count(row).innerText().catch(() => '')).trim()
      const m = txt.match(/\d+/)
      if (m && Number(m[0]) > 0) return row
    }
    return items.first()
  }

  /**
   * The non-empty row whose name sorts first, as the flat lists before the
   * Library tree (#2448) listed them. The tree files rows under their projects,
   * so DOM order is no longer alphabetical; a spec whose fixture choice depends
   * on which entry comes first picks it this way.
   */
  async firstNonEmptyByName(items) {
    const rows = []
    const n = await items.count()
    for (let i = 0; i < n; i++) {
      const row = items.nth(i)
      const name = (await row.locator('.sidebar-list-label-text').first().innerText()).trim()
      const m = (await this.count(row).innerText().catch(() => '')).match(/\d+/)
      if (m && Number(m[0]) > 0) rows.push({ row, name })
    }
    rows.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
    return rows[0]?.row ?? items.first()
  }

  /** A project's row in the Library tree, by name. */
  projectRow(name) {
    return this.projectRows.filter({ hasText: name }).first()
  }

  /** The project's whole branch: its row and, when open, its people and sets. */
  branch(name) {
    return this.page
      .locator('.sidebar-branch')
      .filter({ has: this.projectRow(name) })
  }

  /** Open a project's branch in place, without navigating, if it is closed. */
  async openBranch(name) {
    const row = this.projectRow(name)
    await expect(row).toBeVisible()
    if ((await row.getAttribute('aria-expanded')) !== 'true') {
      await row.locator('.sidebar-branch-toggle').click()
    }
    await expect(row).toHaveAttribute('aria-expanded', 'true')
  }

  /** Open the first project's branch: all of that project. */
  async chooseFirstProject() {
    const project = this.projectRows.first()
    await expect(project).toBeVisible()
    await project.click()
    await expect(project).toHaveAttribute('aria-current', 'page')
  }

  /** Right-click a set row to open the sidebar context menu. */
  async openSetContextMenu(row) {
    await row.click({ button: 'right' })
    await expect(this.ctxMenu).toBeVisible()
  }

  /**
   * A context-menu button by exact accessible name. Exact matching matters for
   * the lock toggle: a substring "Lock set" would also match "Unlock set".
   */
  ctxButton(name) {
    return this.ctxMenu.getByRole('button', { name, exact: true })
  }

  /** Lock a set via its context menu (Lock set → menu closes). */
  async lockSet(row) {
    await this.openSetContextMenu(row)
    await this.ctxButton('Lock set').click()
    await expect(this.ctxMenu).toBeHidden()
  }

  /** Unlock a set via its context menu (Unlock set → menu closes). */
  async unlockSet(row) {
    await this.openSetContextMenu(row)
    await this.ctxButton('Unlock set').click()
    await expect(this.ctxMenu).toBeHidden()
  }
}
