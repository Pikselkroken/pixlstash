import { expect } from '@playwright/test'

/**
 * The notice surface (NoticeHost.vue), built to docs/design/notice-surface.md.
 *
 * These are the acceptance checks the implementing lane could not run because
 * they need a real browser: real layout against the floating SelectionBar pill,
 * both themes, and the --on-dark variant over the lightbox (spec §12 checks
 * 1-3 and 11).
 *
 * ── The store seam ─────────────────────────────────────────────────────────
 * Nothing in the app pushes a *synthetic* notice, and the adoption sites need a
 * real backend failure to fire one — which gives no control over level, count,
 * timing or key. So the specs drive the live Pinia store directly, through a
 * seam installed by `installHooks()` BEFORE navigation.
 *
 * The seam adds NO test-only code to the app. It reaches the running store via
 * three published runtime handles that all survive the production build the e2e
 * backend serves:
 *   - `container.__vue_app__` — set by Vue's own `mount()`; not dev-gated.
 *   - `app.config.globalProperties.$pinia` — set by pinia's `install()`.
 *   - `pinia._s` — pinia's id → store registry; the "notice" store is already
 *     instantiated because NoticeHost.vue mounts with the app.
 * If any of those ever moves, `installHooks` throws with a clear message rather
 * than a spec silently asserting nothing.
 */
export class NoticeHost {
  constructor(page) {
    this.page = page
    this.host = page.locator('.notice-host')
    this.stack = page.locator('.notice-stack')
    this.cards = page.locator('.notice-card')
    this.messages = page.locator('.notice-message')
    this.count = page.locator('.notice-count')
    this.dismissButtons = page.locator('.notice-dismiss')
    this.actionButtons = page.locator('.notice-action')
    // Bottom-edge chrome the stack has to stay clear of (spec §2.2 / §2.4).
    // `.grid-action-pill`, not the old `.floating-selection-bar`: the search bar
    // and the selection pill were merged into one bottom-edge surface, and it is
    // GridActionPill that now owns the anchor and feeds `--floating-bottom-h`.
    // Still selection-driven for these specs (`visible` is
    // `searchActive || selectionActive`, and no search runs here), so the
    // appears-on-select / hides-on-clear assertions still mean what they did.
    this.selectionPill = page.locator('.grid-action-pill')
    this.breadcrumb = page.locator('.grid-breadcrumb')
  }

  /**
   * Install the store seam + the small helpers the specs call. Must run BEFORE
   * `page.goto()` — `addInitScript` only applies to subsequent navigations.
   */
  async installHooks() {
    await this.page.addInitScript(() => {
      const pinia = () => {
        const app = document.querySelector('#app')?.__vue_app__
        const p = app?.config?.globalProperties?.$pinia
        if (!p) {
          throw new Error(
            'e2e: pinia unreachable via #app.__vue_app__.config.globalProperties.$pinia',
          )
        }
        return p
      }
      const store = (id) => {
        const s = pinia()._s?.get(id)
        if (!s) throw new Error(`e2e: pinia store "${id}" not instantiated`)
        return s
      }
      window.__notice = () => store('notice')
      window.__prefs = () => store('userPrefs')
      // Action invocations are counted here so a spec can assert the handler
      // actually ran (handlers cannot cross the evaluate boundary).
      window.__noticeActionCalls = 0
    })
  }

  /**
   * Push a notice. Mirrors `useNoticeStore.push()`; `action` is given as a
   * label only, and its handler increments `window.__noticeActionCalls`.
   *
   * @param {{level?: string, text: string, timeout?: number, key?: string,
   *          actionLabel?: string}} opts
   * @returns {Promise<number|null>} the notice id.
   */
  push(opts) {
    return this.page.evaluate((o) => {
      const { actionLabel, ...rest } = o
      const payload = { ...rest }
      if (actionLabel) {
        payload.action = {
          label: actionLabel,
          handler: () => {
            window.__noticeActionCalls += 1
          },
        }
      }
      return window.__notice().push(payload)
    }, opts)
  }

  /** Push the same payload `times` times (for the coalescing / cap checks). */
  pushMany(opts, times) {
    return this.page.evaluate(
      ({ o, n }) => {
        const ids = []
        for (let i = 0; i < n; i++) ids.push(window.__notice().push({ ...o }))
        return ids
      },
      { o: opts, n: times },
    )
  }

  /** Empty the queue between tests. */
  clear() {
    return this.page.evaluate(() => window.__notice().clear())
  }

  actionCallCount() {
    return this.page.evaluate(() => window.__noticeActionCalls)
  }

  /** How many notices the store holds vs. renders (spec §5 cap + queue). */
  queueState() {
    return this.page.evaluate(() => {
      const s = window.__notice()
      return {
        total: s.notices.length,
        visible: s.visible.map((n) => n.text),
        pending: s.pending.map((n) => n.text),
        maxVisible: s.maxVisible,
      }
    })
  }

  /**
   * Flip the app theme through the real user preference App.vue watches, so
   * Vuetify swaps `--v-theme-*` exactly as it does for a user.
   * @param {'light'|'dark'} mode
   */
  async setTheme(mode) {
    await this.page.evaluate((m) => {
      window.__prefs().themeMode = m
    }, mode)
    // The watcher is synchronous but the class swap lands on the next frame.
    await expect
      .poll(() =>
        this.page.evaluate(
          () => document.querySelector('.v-theme--pixlStashDark') != null,
        ),
      )
      .toBe(mode === 'dark')
  }

  card(text) {
    return this.cards.filter({ hasText: text }).first()
  }

  /**
   * Bounding box read only once it has stopped moving.
   *
   * Every placement assertion needs this. A card enters over --dur-2 with a
   * 12px `translateY` rise, and the host's own `bottom` transitions over
   * --dur-2 whenever the pill's ResizeObserver reports a new height — so a box
   * read the instant the card becomes visible is up to 12px low and can be
   * measured against a stale inset. Polling for two identical reads waits for
   * both transitions without hardcoding a duration.
   */
  async settledBox(locator, { timeout = 6_000 } = {}) {
    let previous = null
    await expect
      .poll(
        async () => {
          const b = await locator.boundingBox()
          const key = b
            ? [b.x, b.y, b.width, b.height].map(Math.round).join(',')
            : 'null'
          const stable = key !== 'null' && key === previous
          previous = key
          return stable
        },
        { timeout, intervals: Array(40).fill(120) },
      )
      .toBe(true)
    return locator.boundingBox()
  }

  /** The single visible card, for the one-card cases. */
  onlyCard() {
    return this.cards.first()
  }

  /**
   * Effective colours of a card, with the `rgba(status, .08)` ::before tint
   * composited over the opaque base — which is what the eye actually sees and
   * what the spec's §8 contrast table is measured against. `backgroundColor`
   * alone would report the untinted base and quietly pass a broken variant.
   */
  colorsOf(cardLocator) {
    return cardLocator.evaluate((card) => {
      const parse = (s) => {
        const m = String(s).match(/[\d.]+/g)
        if (!m || m.length < 3) return null
        return {
          r: +m[0],
          g: +m[1],
          b: +m[2],
          a: m.length > 3 ? +m[3] : 1,
        }
      }
      const over = (fg, bg) => ({
        r: fg.r * fg.a + bg.r * (1 - fg.a),
        g: fg.g * fg.a + bg.g * (1 - fg.a),
        b: fg.b * fg.a + bg.b * (1 - fg.a),
        a: 1,
      })
      const cs = getComputedStyle(card)
      const base = parse(cs.backgroundColor)
      const tint = parse(cs.getPropertyValue('--notice-tint'))
      const effectiveBg = tint ? over(tint, base) : base
      const msg = card.querySelector('.notice-message')
      const glyph = card.querySelector('.notice-glyph')
      const dismiss = card.querySelector('.notice-dismiss')
      const rail = card.querySelector('.notice-rail')
      return {
        bg: effectiveBg,
        base,
        message: parse(getComputedStyle(msg).color),
        glyph: parse(getComputedStyle(glyph).color),
        dismiss: parse(getComputedStyle(dismiss).color),
        rail: parse(getComputedStyle(rail).backgroundColor),
        borderColor: parse(cs.borderTopColor),
        borderRadius: cs.borderTopLeftRadius,
        fontSize: getComputedStyle(msg).fontSize,
        railWidth: getComputedStyle(rail).width,
      }
    })
  }

  /**
   * Is the card the topmost paintable thing at its own centre? The direct test
   * for spec check 11 ("above the overlay chrome") and for the z-order of the
   * stack generally — far more honest than comparing z-index strings across
   * different stacking contexts.
   */
  isTopmostAtCentre(cardLocator) {
    return cardLocator.evaluate((card) => {
      const b = card.getBoundingClientRect()
      const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)
      return hit != null && (hit === card || card.contains(hit))
    })
  }
}

/**
 * Composite a translucent {r,g,b,a} foreground over an opaque background.
 * The dismiss glyph and the ×N count are `rgba(on-surface, .7)`, so their
 * contrast can only be judged after this step.
 */
export function composite(fg, bg) {
  const a = fg.a ?? 1
  return {
    r: fg.r * a + bg.r * (1 - a),
    g: fg.g * a + bg.g * (1 - a),
    b: fg.b * a + bg.b * (1 - a),
    a: 1,
  }
}

/**
 * WCAG relative-luminance contrast ratio between two opaque {r,g,b} colours.
 * Used instead of an axe-core rule because the notice card's real background is
 * a composited ::before tint that axe's colour-contrast rule does not model.
 */
export function contrastRatio(a, b) {
  const lum = (c) => {
    const ch = [c.r, c.g, c.b].map((v) => {
      const s = v / 255
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
  }
  const la = lum(a)
  const lb = lum(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
