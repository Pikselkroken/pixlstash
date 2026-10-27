import { expect } from '@playwright/test'

/**
 * The left sidebar (SideBar.vue) — navigation that filters the grid by picture
 * set (§7), project (§8), and character/person (§9). Thin wrapper around the
 * stable selectors verified in source: .sidebar-set-item, the character rows in
 * .sidebar-character-group, and the project scope in the header slot
 * (SidebarScopeSlot.vue, #2159). Per-row counts live in .sidebar-list-count.
 */
export class SideBar {
  constructor(page) {
    this.page = page
    this.setItems = page.locator('.sidebar-set-item')
    this.characterItems = page.locator('.sidebar-character-group .sidebar-list-item')
    // The header slot's project scope and its "Limit view to" menu (#2159).
    this.scopeButton = page.getByTestId('sidebar-scope')
    this.scopeMenu = page.getByTestId('sidebar-scope-menu')
    // The menu's project rows: every radio item but Any / Not in a project.
    this.scopeProjects = this.scopeMenu
      .getByRole('menuitemradio')
      .filter({ hasNotText: /^(Any project|Not in a project)/ })
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

  /** Open the project scope menu and wait for its rows. */
  async openScopeMenu() {
    await this.scopeButton.click()
    await expect(this.scopeMenu).toBeVisible()
  }

  /** Limit the view to the first project in the scope menu. */
  async chooseFirstProject() {
    await this.openScopeMenu()
    const project = this.scopeProjects.first()
    await expect(project).toBeVisible()
    await project.click()
    await expect(this.scopeMenu).toBeHidden()
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
