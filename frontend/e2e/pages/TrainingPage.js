/**
 * Training a LoRA: the Train a LoRA dialog (TrainLoraDialog.vue), the Training
 * runs view (TrainingRuns.vue, `/models/runs`) and one run's live page
 * (TrainingRunLive.vue, `/models/runs?run=<uuid>`). Locators only.
 */
export class TrainingPage {
  constructor(page) {
    this.page = page
    this.dialog = page.locator('.app-dialog').filter({ hasText: 'Train a LoRA' })
    this.lead = this.dialog.getByTestId('tld-lead')
    this.trigger = this.dialog.getByRole('textbox', { name: 'Trigger word' })
    this.baseModel = this.dialog.getByRole('combobox', { name: 'Base model' })
    this.advanced = this.dialog.getByTestId('tld-advanced')
    this.steps = this.dialog.getByTestId('tld-row-train.steps').getByRole('textbox', { name: 'Steps' })
    this.start = this.dialog.getByTestId('tld-start')
    // The live page's bar: the run's state in words, and its Stop button.
    this.liveStatus = page.locator('.trl-status')
    this.liveStop = page.locator('.trl-bar').getByRole('button', { name: 'Stop training' })
    this.lossChart = page.locator('.trl-chart figure.lora-loss-figure')
    this.liveNotice = page.locator('.trl-notice')
  }

  /** The Base model list's options, open. */
  async baseModelOptions() {
    await this.baseModel.click()
    const options = this.page.getByRole('listbox').getByRole('option')
    await options.first().waitFor()
    const texts = await options.allInnerTexts()
    await this.page.keyboard.press('Escape')
    return texts
  }

  /** A card on Training runs, by the run's name as the card shows it. */
  card(name) {
    return this.page.locator('.tr-card').filter({
      has: this.page.locator('.tr-card-name', { hasText: name }),
    })
  }

  /** One run's live page. */
  async openRun(uuid) {
    await this.page.goto(`/models/runs?run=${uuid}`)
  }
}
