import { expect } from '@playwright/test'

/**
 * The Workflows screen (WorkflowsView.vue): the card grid, a card's verb menu
 * (WorkflowSelectionBar.vue's right-click inventory, `wf-verbs`) and the
 * inspector rail's Workflow tab. A row is `.wfv-row` keyed by the workflow id;
 * the card inside it carries the accessible name.
 */
export class WorkflowsPage {
  constructor(page) {
    this.page = page
    this.rail = page.getByRole('complementary', { name: 'Inspector' })
    this.verbs = page.getByTestId('wf-verbs')
    this.notices = page.locator('.notice-message')
  }

  /** Open the screen and wait for the grid to be drawn. */
  async goto() {
    await this.page.goto('/workflows')
    await expect(this.page.locator('.wfv-row').first()).toBeVisible({ timeout: 15_000 })
  }

  row(id) {
    return this.page.locator(`.wfv-row[data-key="${id}"]`)
  }

  card(id) {
    return this.row(id).getByTestId('workflow-card')
  }

  /** Right-click the card: the menu acts on it alone unless it was selected. */
  async menu(id) {
    await this.card(id).click({ button: 'right' })
    await expect(this.verbs).toBeVisible()
    return this.verbs
  }

  /** A row of the open verb menu, by its `data-verb`. */
  verb(name) {
    return this.verbs.locator(`button[role=menuitem][data-verb="${name}"]`)
  }

  /** Select the card and wait for the rail's Workflow tab to be drawn on it. */
  async openInspector(id) {
    await this.card(id).click()
    await expect(this.row(id)).toHaveAttribute('aria-selected', 'true')
    await expect(this.rail).toBeVisible()
    // Drawn: the detail read has landed. The version chip is no sign, it
    // shows only once there is an earlier version to go back to.
    await expect(this.rail.getByTestId('wftab-row-checkpoint')).toBeVisible()
  }
}
