import { test, expect } from '../fixtures/test.js'
import { composite, contrastRatio } from '../pages/NoticeHost.js'

// docs/design/notice-surface.md §12 acceptance checks.
//
// The store's own behaviour (coalescing, cap + pending queue, pause/resume,
// the action contract, the reading-time floor) is already pinned by the Vitest
// suite in src/stores/useNoticeStore.test.js. What that suite CANNOT see is the
// browser: real layout against the floating SelectionBar pill, both themes,
// composited contrast, z-order over the lightbox, and where focus goes. Those
// are spec checks 1-3, 6-8 and 11, and they are what this file covers. Checks 9
// (screen-reader announcement) and 10 (the OS reduced-motion setting) stay
// manual — see the checklist in the QA report; only their DOM preconditions
// (role/aria-atomic, transition declarations) are asserted here.
//
// Token values referenced below, from src/styles/design-tokens.css:
//   --space-2 4px · --space-3 8px · --space-4 12px · --space-5 16px
//   --notice-max-w 420px · --radius-md 8px · --text-base 14px
const SPACE_3 = 8
const SPACE_4 = 12
const SPACE_5 = 16
const NOTICE_MAX_W = 420

/** Distance from the viewport's bottom edge to the bottom of `box`. */
function bottomInset(viewportHeight, box) {
  return viewportHeight - (box.y + box.height)
}

test.describe('notice surface', () => {
  test.beforeEach(async ({ grid, notices }) => {
    await notices.installHooks()
    await grid.goto()
    // The host is always mounted (last child of .app-viewport), even empty.
    await expect(notices.host).toBeAttached()
  })

  test.afterEach(async ({ notices, page }) => {
    await notices.clear().catch(() => {})
    await notices.setTheme('light').catch(() => {})
    await page.setViewportSize({ width: 1280, height: 720 }).catch(() => {})
  })

  // ── Lifecycle ───────────────────────────────────────────────────────────

  test('renders a notice, shows its message, and dismisses on ✕', async ({
    notices,
  }) => {
    await notices.push({ level: 'info', text: 'Scrapheap restored.' })
    const card = notices.card('Scrapheap restored.')
    await expect(card).toBeVisible()
    await expect(card).toHaveClass(/notice-card--info/)
    // Readable, not a 0-height ghost.
    const box = await card.boundingBox()
    expect(box.height).toBeGreaterThan(30)
    expect(box.width).toBeGreaterThanOrEqual(280)

    await card.locator('.notice-dismiss').click()
    await expect(notices.cards).toHaveCount(0)
  })

  // Spec §6: errors must not auto-dismiss; a failure the user has to act on
  // cannot vanish while they are reading it.
  test('an error never auto-dismisses while a success does', async ({
    notices,
  }) => {
    await notices.push({ level: 'error', text: "Couldn't restore the scrapheap." })
    await notices.push({ level: 'success', text: 'Saved.' })
    await expect(notices.cards).toHaveCount(2)

    // 'Saved.' resolves to max(3000, 2000 + 60*6) = 3360ms.
    await expect(notices.card('Saved.')).toHaveCount(0, { timeout: 9_000 })
    await expect(notices.card("Couldn't restore the scrapheap.")).toBeVisible()
    // Still there well past every level default and the 12s ceiling.
    await notices.page.waitForTimeout(3_000)
    await expect(notices.card("Couldn't restore the scrapheap.")).toBeVisible()
  })

  // Spec §6 rule 1 + check 6 — a 3s window to hit "Undo" fails WCAG 2.2.1.
  test('a success carrying an action never auto-dismisses', async ({
    notices,
  }) => {
    await notices.push({
      level: 'success',
      text: 'Moved 3 pictures to the scrapheap.',
      actionLabel: 'Undo',
    })
    const card = notices.onlyCard()
    await expect(card.locator('.notice-action')).toHaveText('Undo')
    await notices.page.waitForTimeout(6_000)
    await expect(card).toBeVisible()
  })

  // ── Coalescing (check 5) ────────────────────────────────────────────────
  // The bulk-selection case the key exists for: 50 per-picture failures inside
  // one Promise.all must not bury the app under 50 sticky error cards.

  test('50 identical failures collapse into one card with ×50', async ({
    notices,
  }) => {
    await notices.pushMany(
      { level: 'error', text: "Couldn't delete the picture.", key: 'bulk-delete' },
      50,
    )
    await expect(notices.cards).toHaveCount(1)
    await expect(notices.count).toHaveText('×50')
    await expect(notices.count).toHaveAttribute('aria-label', '50 occurrences')
    // Different keys stay apart — coalescing must not swallow unrelated news.
    await notices.push({ level: 'error', text: 'Import failed.', key: 'import' })
    await expect(notices.cards).toHaveCount(2)
  })

  // ── Cap + pending queue (check 4) ───────────────────────────────────────

  test('renders at most 3 and a queued notice never expires unseen', async ({
    notices,
  }) => {
    // Six successes, each resolving to max(3000, 2000 + 60*len) ≈ 3.5s.
    for (let i = 0; i < 6; i++) {
      await notices.push({ level: 'success', text: `queued ${i}` })
    }
    let state = await notices.queueState()
    expect(state.maxVisible).toBe(3)
    expect(state.total).toBe(6)
    expect(state.visible).toEqual(['queued 0', 'queued 1', 'queued 2'])
    await expect(notices.cards).toHaveCount(3)
    // Newest of the visible three nearest the bottom edge (spec §5).
    const tops = await notices.cards.evaluateAll((els) =>
      els.map((e) => e.getBoundingClientRect().top),
    )
    expect(tops).toEqual([...tops].sort((a, b) => a - b))

    // The three visible ones expire; the queue promotes the next three.
    await expect(notices.card('queued 0')).toHaveCount(0, { timeout: 10_000 })
    await expect(notices.card('queued 3')).toBeVisible()
    state = await notices.queueState()
    expect(state.visible).toEqual(['queued 3', 'queued 4', 'queued 5'])

    // The load-bearing assertion: 'queued 3' was pushed at t=0 alongside the
    // others. If its timer had started at push time it would already be gone.
    // It must survive a full fresh window from PROMOTION.
    await notices.page.waitForTimeout(1_500)
    await expect(notices.card('queued 3')).toBeVisible()
  })

  // ── Pause / resume (check 7, WCAG 2.2.1) ────────────────────────────────

  test('hovering a card stops its countdown; leaving resumes it', async ({
    page,
    notices,
  }) => {
    await notices.push({ level: 'success', text: 'Hover me.' })
    const card = notices.card('Hover me.')
    await expect(card).toBeVisible()
    await card.hover()
    // Far past the ~3.4s the message would otherwise get.
    await page.waitForTimeout(7_000)
    await expect(card).toBeVisible()

    await page.mouse.move(5, 5)
    await expect(card).toHaveCount(0, { timeout: 8_000 })
  })

  test('focus inside a card stops its countdown', async ({ page, notices }) => {
    await notices.push({ level: 'success', text: 'Focus me.' })
    const card = notices.card('Focus me.')
    await card.locator('.notice-dismiss').focus()
    await page.waitForTimeout(7_000)
    await expect(card).toBeVisible()

    await page.evaluate(() => document.activeElement?.blur())
    await expect(card).toHaveCount(0, { timeout: 8_000 })
  })

  // ── Placement: the crux (checks 1, 2, 3) ────────────────────────────────
  // Asserted with bounding boxes, not screenshots: the contract is arithmetic
  // (`--notice-safe-bottom` = --space-5 + measured pill height + --space-3),
  // and a pixel diff would only tell us *that* something moved, not whether the
  // stack still clears the pill.

  for (const theme of ['light', 'dark']) {
    test(`sits clear above the selection pill (${theme} theme)`, async ({
      page,
      grid,
      notices,
    }) => {
      await notices.setTheme(theme)
      await grid.thumbnails.nth(0).click({ modifiers: ['ControlOrMeta'] })
      await grid.thumbnails.nth(1).click({ modifiers: ['ControlOrMeta'] })
      await expect(notices.selectionPill).toBeVisible()

      await notices.push({ level: 'error', text: 'Deleting 12 pictures failed.' })
      const card = notices.onlyCard()
      await expect(card).toBeVisible()

      const cardBox = await notices.settledBox(card)
      const pillBox = await notices.selectionPill.boundingBox()
      const viewport = page.viewportSize()

      // 1. No overlap, at all. This is the whole point of the contract.
      expect(cardBox.y + cardBox.height).toBeLessThanOrEqual(pillBox.y + 0.5)
      // 2. And exactly --space-3 above it: pill bottom inset is --space-5, the
      //    stack's is --space-5 + measured pill height + --space-3.
      const gap = pillBox.y - (cardBox.y + cardBox.height)
      expect(gap).toBeGreaterThan(SPACE_3 - 2)
      expect(gap).toBeLessThan(SPACE_3 + 2)
      // 3. Derived from a MEASURED pill height, not a constant.
      const inset = bottomInset(viewport.height, cardBox)
      expect(inset).toBeGreaterThan(SPACE_5 + pillBox.height + SPACE_3 - 2)
      expect(inset).toBeLessThan(SPACE_5 + pillBox.height + SPACE_3 + 2)
      // 4. Centred on the viewport, capped at --notice-max-w (spec §2.2/§2.3:
      //    the host deliberately does NOT track the sidebar).
      expect(cardBox.width).toBeLessThanOrEqual(NOTICE_MAX_W + 1)
      const centreOffset = Math.abs(
        cardBox.x + cardBox.width / 2 - viewport.width / 2,
      )
      expect(centreOffset).toBeLessThan(2)
    })
  }

  test('settles back to the base inset when the selection clears', async ({
    page,
    grid,
    notices,
  }) => {
    await grid.thumbnails.nth(0).click({ modifiers: ['ControlOrMeta'] })
    await expect(notices.selectionPill).toBeVisible()
    await notices.push({ level: 'error', text: 'Still here after the pill goes.' })
    const card = notices.onlyCard()
    const raised = await notices.settledBox(card)
    const viewport = page.viewportSize()
    expect(bottomInset(viewport.height, raised)).toBeGreaterThan(SPACE_5 + 10)

    // Deselect: the pill leaves, the stack settles back to --space-5.
    await grid.thumbnails.nth(0).click({ modifiers: ['ControlOrMeta'] })
    await expect(notices.selectionPill).toBeHidden()
    await expect
      .poll(
        async () => {
          const b = await card.boundingBox()
          return Math.round(bottomInset(viewport.height, b))
        },
        { timeout: 5_000 },
      )
      .toBeLessThanOrEqual(SPACE_5 + 1)
    // The notice survived the pill's exit — it never rides on the selection.
    await expect(card).toBeVisible()
  })

  // Check 3 — at 375px the card goes edge-to-edge minus --space-4 gutters, the
  // cap drops to 2, and the bottom-left breadcrumb joins the calculation.
  test('narrow viewport: cap drops to 2, gutters shrink, breadcrumb clears', async ({
    page,
    notices,
  }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    // The host re-caps from its own matchMedia listener.
    await expect.poll(async () => (await notices.queueState()).maxVisible).toBe(2)

    for (let i = 0; i < 4; i++) {
      await notices.push({ level: 'error', text: `narrow ${i}` })
    }
    await expect(notices.cards).toHaveCount(2)
    const state = await notices.queueState()
    expect(state.total).toBe(4)
    expect(state.pending).toHaveLength(2)

    const cardBox = await notices.settledBox(notices.cards.first())
    expect(Math.round(cardBox.width)).toBe(375 - 2 * SPACE_4)
    expect(Math.round(cardBox.x)).toBe(SPACE_4)

    // The centred card now covers the bottom-left breadcrumb's column, so the
    // breadcrumb contributes to --floating-bottom-h (spec §2.4).
    const bcCount = await notices.breadcrumb.count()
    expect(
      bcCount,
      'breadcrumb should render at 375px — if this fails the narrow-viewport ' +
        'contributor to --floating-bottom-h is untested, not passing',
    ).toBeGreaterThan(0)
    const bcBox = await notices.breadcrumb.first().boundingBox()
    const lowest = await notices.settledBox(notices.cards.last())
    expect(lowest.y + lowest.height).toBeLessThanOrEqual(bcBox.y + 0.5)
  })

  // ── Over the lightbox (check 11) ────────────────────────────────────────

  test('takes the --on-dark variant over the lightbox and paints above it', async ({
    notices,
    overlay,
  }) => {
    await overlay.openFromGrid()
    await expect(overlay.root).toBeVisible()

    await notices.push({ level: 'error', text: 'Description save failed.' })
    const card = notices.onlyCard()
    await expect(card).toBeVisible()
    await expect(notices.host).toHaveClass(/notice-host--on-dark/)

    const c = await notices.colorsOf(card)
    // dark-surface #242628 — the card swaps its base, not just its text.
    expect(c.base).toMatchObject({ r: 36, g: 38, b: 40 })
    // on-dark-surface #f2e5da on the 14%-tinted dark card: spec §2.5 measures
    // 9.86:1 – 11.03:1 across the four variants.
    expect(contrastRatio(composite(c.message, c.bg), c.bg)).toBeGreaterThan(4.5)
    // Above the lightbox chrome (z 1000–5000), verified by hit-testing rather
    // than by comparing z-index strings across stacking contexts.
    expect(await notices.isTopmostAtCentre(card)).toBe(true)

    await overlay.close()
    await expect(notices.host).not.toHaveClass(/notice-host--on-dark/)
    // Closing the lightbox must not take the notice with it.
    await expect(card).toBeVisible()
  })

  // ── Accessibility: the automatable half (checks 8, 9) ───────────────────

  test('error is role=alert, everything else role=status, host announces nothing', async ({
    notices,
  }) => {
    await notices.push({ level: 'error', text: 'Boom.' })
    await notices.push({ level: 'warning', text: '12 deleted, 3 are frozen.' })
    await notices.push({ level: 'success', text: 'Done.' })

    await expect(notices.card('Boom.')).toHaveAttribute('role', 'alert')
    await expect(notices.card('12 deleted, 3 are frozen.')).toHaveAttribute(
      'role',
      'status',
    )
    await expect(notices.card('Done.')).toHaveAttribute('role', 'status')
    for (const card of await notices.cards.all()) {
      await expect(card).toHaveAttribute('aria-atomic', 'true')
    }
    // The host must not double-announce its children (spec §8).
    expect(await notices.host.getAttribute('role')).toBeNull()
    expect(await notices.host.getAttribute('aria-live')).toBeNull()
    await expect(notices.dismissButtons.first()).toHaveAttribute(
      'aria-label',
      'Dismiss notification',
    )
  })

  // Check 8 — a notice appears while the user is mid-task and must not move
  // their cursor. No autofocus, no focus trap, no programmatic .focus().
  test('focus is never stolen when a notice appears', async ({ page, grid, notices }) => {
    await grid.searchButton.focus()
    const before = await page.evaluate(() => ({
      tag: document.activeElement?.tagName,
      title: document.activeElement?.getAttribute('title'),
    }))
    expect(before.tag).toBe('BUTTON')

    await notices.push({ level: 'error', text: 'Appeared behind your back.' })
    await expect(notices.onlyCard()).toBeVisible()
    // Give an errant autofocus a frame or two to fire.
    await page.waitForTimeout(400)
    const after = await page.evaluate(() => ({
      tag: document.activeElement?.tagName,
      title: document.activeElement?.getAttribute('title'),
      inNotice: !!document.activeElement?.closest('.notice-host'),
    }))
    expect(after.inNotice).toBe(false)
    expect(after).toMatchObject({ tag: before.tag, title: before.title })
  })

  test('action and dismiss are keyboard-reachable with a visible focus ring', async ({
    page,
    notices,
  }) => {
    await notices.push({
      level: 'success',
      text: 'Moved to the scrapheap.',
      actionLabel: 'Undo',
    })
    const card = notices.onlyCard()
    const action = card.locator('.notice-action')
    const dismiss = card.locator('.notice-dismiss')

    await action.focus()
    // A real Tab keypress, so :focus-visible matches as it does for a keyboard
    // user (a programmatic .focus() alone does not reliably match it).
    await page.keyboard.press('Tab')
    await expect(dismiss).toBeFocused()
    const ring = await dismiss.evaluate((el) => getComputedStyle(el).boxShadow)
    expect(ring).not.toBe('none')

    // Hit area: 24×24 visual box expanded to 40×40 (WCAG 2.5.8 floor is 24×24).
    const hit = await dismiss.evaluate((el) => {
      const b = el.getBoundingClientRect()
      const before = getComputedStyle(el, '::before')
      return { w: b.width, h: b.height, inset: before.inset }
    })
    // 23.9999… in Chromium: the 24px box is laid out sub-pixel.
    expect(hit.w).toBeGreaterThanOrEqual(23.9)
    expect(hit.h).toBeGreaterThanOrEqual(23.9)
    expect(hit.inset).toBe('-8px') // ::before expansion → 40×40 (WCAG 2.5.8)

    await page.keyboard.press('Shift+Tab')
    await expect(action).toBeFocused()
    await page.keyboard.press('Enter')
    expect(await notices.actionCallCount()).toBe(1)
    await expect(notices.cards).toHaveCount(0)
  })

  // Esc dismisses the newest notice ONLY while focus is inside the host (§6).
  test('Escape dismisses the newest card only from inside the host', async ({
    page,
    grid,
    notices,
  }) => {
    await notices.push({ level: 'error', text: 'older' })
    await notices.push({ level: 'error', text: 'newer' })

    // Focus outside the host: Esc belongs to the grid, not to us.
    await grid.searchButton.focus()
    await page.keyboard.press('Escape')
    await expect(notices.cards).toHaveCount(2)

    await notices.card('newer').locator('.notice-dismiss').focus()
    await page.keyboard.press('Escape')
    await expect(notices.cards).toHaveCount(1)
    await expect(notices.card('older')).toBeVisible()
  })

  // ── Contrast + token conformance (spec §8 table, check 12) ──────────────
  // Stands in for an axe-core colour-contrast run: axe is not a dependency of
  // this repo (see the QA report) and, more to the point, it would measure the
  // card's untinted `background-color` and miss the rgba(status,.08) ::before
  // layer that is the card's real background.

  for (const theme of ['light', 'dark']) {
    test(`every variant clears its contrast floor and stays on tokens (${theme})`, async ({
      notices,
    }) => {
      await notices.setTheme(theme)
      for (const level of ['info', 'success', 'warning', 'error']) {
        await notices.clear()
        await notices.push({ level, text: `A ${level} notice.` })
        const card = notices.onlyCard()
        await expect(card).toBeVisible()
        const c = await notices.colorsOf(card)
        const where = `${theme}/${level}`

        // Message and action label: 4.5:1 (spec measures 10.6 – 15.0).
        expect(
          contrastRatio(composite(c.message, c.bg), c.bg),
          `message ${where}`,
        ).toBeGreaterThanOrEqual(4.5)
        // Glyph: 3:1 non-text floor.
        expect(
          contrastRatio(composite(c.glyph, c.bg), c.bg),
          `glyph ${where}`,
        ).toBeGreaterThanOrEqual(3.0)
        // Dismiss glyph at 0.7 alpha: spec measures 5.60 / 6.32.
        expect(
          contrastRatio(composite(c.dismiss, c.bg), c.bg),
          `dismiss ${where}`,
        ).toBeGreaterThanOrEqual(3.0)
        // The rail is decorative reinforcement (the glyph shape carries the
        // variant), but it must still be a visible mark: spec measures
        // 2.59 – 4.72 across the eight combinations.
        expect(
          contrastRatio(composite(c.rail, c.bg), c.bg),
          `rail ${where}`,
        ).toBeGreaterThanOrEqual(2.5)

        // Check 12 — no off-ramp values in the new component.
        expect(c.borderRadius, `radius ${where}`).toBe('8px') // --radius-md
        expect(c.fontSize, `type ${where}`).toBe('14px') // --text-base
        expect(c.railWidth, `rail width ${where}`).toBe('4px') // --space-2
      }
    })
  }

  // ── Motion (check 10) ───────────────────────────────────────────────────
  // Chromium's `prefers-reduced-motion` emulation makes the CSS half of check
  // 10 automatable. What stays manual is the perceptual half — that nothing
  // visibly flickers with the OS setting on (see the QA report checklist).
  //
  // The enter/leave transforms are read off a throwaway clone of a live card:
  // the styles are scoped (`[data-v-…]`), so a clone carries the scope
  // attribute and resolves .notice-enter-from / .notice-leave-to exactly as the
  // real transition class would.
  test('enter rises 12px from below and leave slides 4px, motion allowed', async ({
    page,
    notices,
  }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await notices.push({ level: 'info', text: 'Rising into view.' })
    const card = notices.onlyCard()
    await expect(card).toBeVisible()

    // The host's inset transitions, so the stack settles rather than jumping
    // when the pill appears or goes (spec §7 "safe-bottom change").
    expect(
      await notices.host.evaluate((el) => getComputedStyle(el).transitionProperty),
    ).toContain('bottom')

    const probe = await card.evaluate((el) => {
      const read = (cls) => {
        const clone = el.cloneNode(true)
        clone.classList.add(cls)
        clone.style.visibility = 'hidden'
        el.parentElement.appendChild(clone)
        const t = getComputedStyle(clone).transform
        clone.remove()
        return t
      }
      return { enter: read('notice-enter-from'), leave: read('notice-leave-to') }
    })
    // translateY(--space-4) = 12px in, translateY(--space-2) = 4px out.
    expect(probe.enter).toBe('matrix(1, 0, 0, 1, 0, 12)')
    expect(probe.leave).toBe('matrix(1, 0, 0, 1, 0, 4)')
  })

  test('reduced motion removes all vertical travel and the inset slide', async ({
    page,
    notices,
  }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await notices.push({ level: 'info', text: 'No travel, please.' })
    const card = notices.onlyCard()
    await expect(card).toBeVisible()

    const probe = await card.evaluate((el) => {
      const read = (cls) => {
        const clone = el.cloneNode(true)
        clone.classList.add(cls)
        clone.style.visibility = 'hidden'
        el.parentElement.appendChild(clone)
        const style = getComputedStyle(clone)
        const out = { transform: style.transform, transition: style.transitionProperty }
        clone.remove()
        return out
      }
      return {
        enter: read('notice-enter-from'),
        leave: read('notice-leave-to'),
        move: read('notice-move'),
      }
    })
    // A zero-duration transition still SNAPS a translateY into place; the
    // component's own @media block has to null the transform out, which is the
    // step design-tokens.css cannot do for it (spec §7).
    expect(probe.enter.transform).toBe('none')
    expect(probe.leave.transform).toBe('none')
    expect(probe.move.transition).toBe('none')
    expect(
      await notices.host.evaluate((el) => getComputedStyle(el).transitionProperty),
    ).toBe('none')

    await page.emulateMedia({ reducedMotion: 'no-preference' })
  })
})
