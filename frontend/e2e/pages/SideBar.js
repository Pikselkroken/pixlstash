import { expect } from '@playwright/test'

/**
 * The left sidebar (SideBar.vue) — navigation that filters the grid by picture
 * set (§7), project (§8), and character/person (§9). Thin wrapper around the
 * stable selectors verified in source: .sidebar-set-item, the character rows in
 * .sidebar-character-group, the "Projects" .sidebar-view-tab, and the project
 * tree's .sidebar-project-tree-row. Per-row counts live in .sidebar-list-count.
 */
export class SideBar {
  constructor(page) {
    this.page = page
    this.setItems = page.locator('.sidebar-set-item')
    this.characterItems = page.locator('.sidebar-character-group .sidebar-list-item')
    this.projectsTab = page.locator('.sidebar-view-tab', { hasText: 'Projects' }).first()
    this.projectRows = page.locator('.sidebar-project-tree-row')
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

  /** Switch the sidebar to the Projects tree and wait for it to render. */
  async openProjectsTab() {
    await this.projectsTab.click()
    await expect(this.projectRows.first()).toBeVisible()
  }
}
