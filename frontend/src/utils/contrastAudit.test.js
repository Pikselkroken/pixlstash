import { describe, it, expect } from 'vitest'
import {
  auditContrast,
  composite,
  contrastRatio,
  parseHex,
  readThemeColors,
  toHex,
} from './contrastAudit.js'

// Regression gate for the 2026-07-26 palette incident: deepening the status
// hues in one pass broke `dark-surface-<status>`, which is a FOREGROUND family
// (11px semibold labels in ReviewRail.vue on chrome that stays dark in both
// themes). Three of four fell to 2.48:1 – 2.97:1 and shipped to develop,
// because the only contrast coverage in the repo was the Playwright notice-card
// spec - it renders the rail and nothing else.
//
// These assert the arithmetic on every pair, so a token edit that breaks a pair
// no test happens to render still fails here.

describe('contrast audit helpers', () => {
  it('parses hex with and without a leading # and stray whitespace', () => {
    expect(parseHex('#b54538')).toEqual({ r: 181, g: 69, b: 56 })
    expect(parseHex('b54538')).toEqual({ r: 181, g: 69, b: 56 })
    // A trailing-space typo shipped in main.js once ("#b0392b  "); Vuetify's
    // parseHex silently rewrote the spaces to an FF alpha rather than erroring.
    expect(parseHex('#b0392b  ')).toEqual({ r: 176, g: 57, b: 43 })
  })

  it('rejects anything that is not a 6-digit hex', () => {
    expect(() => parseHex('#fff')).toThrow(/6-digit hex/)
    expect(() => parseHex('rebeccapurple')).toThrow(/6-digit hex/)
  })

  it('computes the WCAG reference ratios', () => {
    // The two anchors of the scale: identical colours are 1:1, black on white
    // is 21:1.
    expect(contrastRatio('#ffffff', '#ffffff')).toBeCloseTo(1, 5)
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5)
    // Order must not matter.
    expect(contrastRatio('#b54538', '#ffffff')).toBeCloseTo(
      contrastRatio('#ffffff', '#b54538'),
      10,
    )
  })

  it('matches the ratio Playwright measured for the failing rail', () => {
    // frontend/e2e/specs/notice-surface.spec.js reported exactly this for
    // dark/info before the fix. Same formula, so the audit and the e2e suite
    // can never disagree about a value.
    const bg = composite('#2f6690', '#23282f', 0.08)
    expect(contrastRatio('#2f6690', bg)).toBeCloseTo(2.2762013, 5)
  })

  it('composites a translucent hue over its background', () => {
    expect(toHex(composite('#ffffff', '#000000', 0))).toBe('#000000')
    expect(toHex(composite('#ffffff', '#000000', 1))).toBe('#ffffff')
    expect(toHex(composite('#ffffff', '#000000', 0.5))).toBe('#808080')
  })

  it('keeps composited channels fractional', () => {
    // Rounding here would desync the audit from the Playwright assertion by
    // ~0.002, which is enough to matter for a pair sitting on its floor.
    expect(composite('#ffffff', '#000000', 0.5)).toEqual({
      r: 127.5,
      g: 127.5,
      b: 127.5,
    })
  })
})

describe('main.js status tokens', () => {
  const themes = readThemeColors()

  it('defines both themes with all four status families', () => {
    for (const theme of ['light', 'dark']) {
      for (const level of ['error', 'warning', 'success', 'info']) {
        expect(themes[theme][level], `${theme}.${level}`).toMatch(/^#[0-9a-f]{6}$/i)
        expect(themes[theme][`on-${level}`], `${theme}.on-${level}`).toMatch(
          /^#[0-9a-f]{6}$/i,
        )
        expect(
          themes[theme][`dark-surface-${level}`],
          `${theme}.dark-surface-${level}`,
        ).toMatch(/^#[0-9a-f]{6}$/i)
      }
    }
  })

  it('keeps dark-surface-<status> identical across the two themes', () => {
    // A dark-surface stays dark in both themes, so its status hues have no
    // reason to differ. Divergence here means someone edited one theme only.
    for (const level of ['error', 'warning', 'success', 'info']) {
      expect(
        themes.dark[`dark-surface-${level}`],
        `dark-surface-${level} differs between themes`,
      ).toBe(themes.light[`dark-surface-${level}`])
    }
  })

  it('clears every contrast floor', () => {
    const failures = auditContrast(themes)
      .filter((row) => !row.pass)
      .map(
        (row) =>
          `${row.group} - ${row.name}: ${row.fg} on ${row.bg} is ` +
          `${row.ratio.toFixed(2)}:1, floor ${row.floor}:1`,
      )
    expect(failures).toEqual([])
  })

  it('measures every pair it claims to', () => {
    // 2 themes x 4 levels x (fill label + notice rail + dark-chrome text +
    // dark-chrome rail) = 32. Guards against a loop silently skipping a family.
    expect(auditContrast(themes)).toHaveLength(32)
  })
})
