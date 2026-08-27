/**
 * Contrast audit for the status-colour token families in `src/main.js`.
 *
 * Why this exists: on 2026-07-26 the dark-mode status hues were deepened in one
 * pass. That was right for the FILL tier and wrong for `dark-surface-<status>`,
 * which is a FOREGROUND family - it styles 11px semibold labels on chrome that
 * stays dark in both themes. Three of the four dropped to 2.48:1 – 2.97:1 and
 * nothing caught it, because a token pair is only measured if some test happens
 * to render it. The Playwright suite covers the notice card and nothing else.
 *
 * So the hues are parsed straight out of `main.js` rather than duplicated here:
 * a value that changes there is re-measured here on the next run, and cannot
 * drift out of sync the way the hand-written `// 5.51:1` comments did.
 *
 * Run it:      npm run audit:contrast
 * Enforced by: src/utils/contrastAudit.test.js
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const MAIN_JS = resolve(HERE, '../main.js')

const LEVELS = ['error', 'warning', 'success', 'info']

/** Surfaces a status hue is rendered against, per theme. */
const THEME_SURFACE = { light: '#ffffff', dark: '#23282f' }

/**
 * The deliberately-dark surfaces. These stay dark in BOTH themes, which is the
 * whole reason `dark-surface-<status>` is a separate family.
 */
const DARK_CHROME = { light: '#242628', dark: '#181b20' }

/** Tint alphas the notice host composites a status hue at (NoticeHost.vue). */
const TINT_ON_THEME_SURFACE = 0.08
const TINT_ON_DARK_CHROME = 0.14

/**
 * WCAG floors. 4.5 is the normal-text floor: `dark-surface-<status>` styles
 * `--text-2xs` (11px) semibold labels in ReviewRail.vue, which is not large
 * text. 2.5 is the notice rail's authored floor - it is decorative
 * reinforcement (the glyph carries the variant) but must still read as a mark.
 */
const FLOOR_TEXT = 4.5
const FLOOR_RAIL = 2.5

/** `#rrggbb` → `{r,g,b}`. Tolerates stray whitespace in the source value. */
export function parseHex(value) {
  const h = String(value).trim().replace(/^#/, '')
  if (!/^[0-9a-f]{6}$/i.test(h)) {
    throw new Error(`Not a 6-digit hex colour: ${JSON.stringify(value)}`)
  }
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  }
}

/** WCAG 2.x relative luminance. Matches `frontend/e2e/pages/NoticeHost.js`. */
function luminance(c) {
  const ch = [c.r, c.g, c.b].map((v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
}

/** Accept either `#rrggbb` or an already-parsed `{r,g,b}`. */
function toRgb(value) {
  return typeof value === 'string' ? parseHex(value) : value
}

/** WCAG contrast ratio between two opaque colours, either order. */
export function contrastRatio(a, b) {
  const la = luminance(toRgb(a))
  const lb = luminance(toRgb(b))
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/**
 * Composite a translucent hue over an opaque background.
 *
 * Channels stay FRACTIONAL. The e2e helper does the same, and rounding to 8-bit
 * here would shift a ratio by ~0.002 - enough for this audit and the Playwright
 * assertion to disagree about a pair sitting on its floor. Use `toHex` when a
 * composited colour needs to be displayed.
 */
export function composite(fg, bg, alpha) {
  const f = toRgb(fg)
  const b = toRgb(bg)
  const mix = (x, y) => x * alpha + y * (1 - alpha)
  return { r: mix(f.r, b.r), g: mix(f.g, b.g), b: mix(f.b, b.b) }
}

/** `{r,g,b}` → `#rrggbb`, rounding to 8-bit only at the point of display. */
export function toHex(c) {
  return (
    '#' +
    [c.r, c.g, c.b]
      .map((v) => Math.round(v).toString(16).padStart(2, '0'))
      .join('')
  )
}

/**
 * Pull the two `colors: { … }` blocks out of `main.js`.
 *
 * A regex rather than an import because `main.js` boots the whole app (Vue,
 * Vuetify, the router) the moment it is evaluated. The block is a flat map of
 * string literals, so there is nothing here a parser would do better.
 */
export function readThemeColors(source = readFileSync(MAIN_JS, 'utf8')) {
  const themes = {}
  for (const [name, marker] of [
    ['light', 'pixlStashLight'],
    ['dark', 'pixlStashDark'],
  ]) {
    const start = source.indexOf(`const ${marker} = {`)
    if (start === -1) throw new Error(`Could not find "const ${marker}" in main.js`)
    const open = source.indexOf('colors: {', start)
    if (open === -1) throw new Error(`Could not find the colors block for ${marker}`)
    const close = source.indexOf('\n  },', open)
    if (close === -1) throw new Error(`Unterminated colors block for ${marker}`)

    const block = source.slice(open, close)
    const colors = {}
    // `key: "#hex"` or `"quoted-key": "#hex"`, ignoring trailing comments.
    for (const m of block.matchAll(/^\s*"?([\w-]+)"?:\s*"([^"]+)"/gm)) {
      colors[m[1]] = m[2]
    }
    if (Object.keys(colors).length === 0) {
      throw new Error(`Parsed no colours for ${marker} - has main.js changed shape?`)
    }
    themes[name] = colors
  }
  return themes
}

/**
 * Measure every status pair in both themes.
 *
 * @returns {Array<{group,name,fg,bg,ratio,floor,pass}>} one row per pair.
 */
export function auditContrast(themes = readThemeColors()) {
  const rows = []
  const add = (group, name, fg, bg, floor) => {
    const ratio = contrastRatio(fg, bg)
    rows.push({
      group,
      name,
      fg: typeof fg === 'string' ? fg : toHex(fg),
      bg: typeof bg === 'string' ? bg : toHex(bg),
      ratio,
      floor,
      pass: ratio >= floor,
    })
  }

  for (const theme of ['light', 'dark']) {
    const c = themes[theme]
    const surface = THEME_SURFACE[theme]
    const chrome = DARK_CHROME[theme]

    const need = (key) => {
      const v = c[key]
      if (!v) throw new Error(`${theme} theme is missing "${key}"`)
      return v
    }

    // The authored label on its solid fill. This is the pair the stale
    // `// 5.51:1` comments described, and the one nothing was checking.
    for (const level of LEVELS) {
      add(
        `${theme} · fill label`,
        `on-${level} on ${level}`,
        need(`on-${level}`),
        need(level),
        FLOOR_TEXT,
      )
    }

    // The notice rail: the hue against the card it tints. Mirrors
    // NoticeHost.vue's `--notice-status` over `--notice-tint`.
    for (const level of LEVELS) {
      const fill = need(level)
      add(
        `${theme} · notice rail`,
        `${level} rail`,
        fill,
        composite(fill, surface, TINT_ON_THEME_SURFACE),
        FLOOR_RAIL,
      )
    }

    // The foreground family, on the chrome that stays dark in this theme.
    for (const level of LEVELS) {
      const onDark = need(`dark-surface-${level}`)
      add(
        `${theme} · on dark chrome (${chrome})`,
        `dark-surface-${level} text`,
        onDark,
        chrome,
        FLOOR_TEXT,
      )
      add(
        `${theme} · on dark chrome (${chrome})`,
        `dark-surface-${level} rail`,
        onDark,
        composite(onDark, chrome, TINT_ON_DARK_CHROME),
        FLOOR_RAIL,
      )
    }
  }
  return rows
}

/** Pretty-print the audit. Returns the process exit code. */
function reportContrast(rows = auditContrast(), log = console.log) {
  let lastGroup = null
  let failures = 0

  for (const row of rows) {
    if (row.group !== lastGroup) {
      log(`\n  ${row.group}`)
      log(`  ${'-'.repeat(64)}`)
      lastGroup = row.group
    }
    if (!row.pass) failures += 1
    const verdict = row.pass ? 'ok  ' : 'FAIL'
    log(
      `  ${verdict}  ${row.name.padEnd(30)} ` +
        `${row.fg} on ${row.bg}  ` +
        `${row.ratio.toFixed(2).padStart(5)} / ${row.floor.toFixed(1)}`,
    )
  }

  log('')
  if (failures === 0) {
    log(`  All ${rows.length} pairs clear their floors.\n`)
    return 0
  }
  log(`  ${failures} of ${rows.length} pairs are below their floor.\n`)
  return 1
}

// CLI entry point: `npm run audit:contrast`.
if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = reportContrast()
}
