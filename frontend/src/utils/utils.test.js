import { describe, it, expect, vi } from 'vitest'
import {
  debounce,
  toggleScore,
  formatUserDate,
  formatUserDay,
  getStackThreshold,
  arraysEqualByString,
  isRangeOverlap,
  rangeCovers,
  extractComfyuiExecutionErrorMessage,
  formatComfyuiExecutionErrorMessage,
  isComfyuiOutOfMemoryMessage,
  normalizePluginProgressMessage,
  getStackColorIndexFromId,
  applyStackBackgroundAlpha,
  applyStackBadgeTint,
  getStackColor,
} from './utils.js'
import { composite, contrastRatio } from './contrastAudit.js'

describe('toggleScore', () => {
  it('returns target when current differs', () => {
    expect(toggleScore(0, 5)).toBe(5)
  })

  it('returns 0 when current equals target (toggle off)', () => {
    expect(toggleScore(5, 5)).toBe(0)
  })

  it('returns current unchanged for non-finite target (Infinity)', () => {
    expect(toggleScore(3, Infinity)).toBe(3)
    expect(toggleScore(3, -Infinity)).toBe(3)
  })

  it('coerces NaN target to 0 (via falsy coercion)', () => {
    expect(toggleScore(3, NaN)).toBe(0)
    expect(toggleScore(3, undefined)).toBe(0)
  })

  it('coerces string numbers', () => {
    expect(toggleScore('3', '3')).toBe(0)
    expect(toggleScore('2', '5')).toBe(5)
  })
})

describe('formatUserDate', () => {
  it('returns empty string for falsy input', () => {
    expect(formatUserDate('', 'iso')).toBe('')
    expect(formatUserDate(null, 'iso')).toBe('')
  })

  it('returns original string for invalid date', () => {
    expect(formatUserDate('not-a-date', 'iso')).toBe('not-a-date')
  })

  it('formats iso correctly', () => {
    // Use a UTC-fixed date to avoid timezone variance in CI
    const result = formatUserDate('2024-06-15T10:30:00Z', 'iso')
    expect(result).toMatch(/2024/)
    expect(result).toMatch(/06/)
    expect(result).toMatch(/15/)
  })

  it('returns a non-empty string for all known format keys', () => {
    const formats = ['us', 'british', 'eu', 'ymd-slash', 'ymd-dot', 'ymd-jp', 'locale', 'iso']
    for (const fmt of formats) {
      expect(formatUserDate('2024-01-20T09:05:00Z', fmt)).toBeTruthy()
    }
  })
})

describe('formatUserDay', () => {
  // An instant built from LOCAL noon, so the day asserted is the day every
  // runner's clock is on rather than the one before it east of the date line.
  const NOON = new Date(2024, 0, 20, 12, 0).toISOString()

  it('writes the day each format writes, and no clock', () => {
    // Fixed expected strings, never a comparison against `formatUserDate`'s own
    // output: the point of this function is that it is BUILT from the parts
    // rather than cut out of a formatted stamp, and a test that trimmed the
    // stamp the same way would agree with any trimming bug.
    expect(formatUserDay(NOON, 'iso')).toBe('2024-01-20')
    expect(formatUserDay(NOON, 'us')).toBe('01/20/2024')
    expect(formatUserDay(NOON, 'british')).toBe('20/01/2024')
    expect(formatUserDay(NOON, 'eu')).toBe('20/01/2024')
    expect(formatUserDay(NOON, 'ymd-slash')).toBe('2024/01/20')
    expect(formatUserDay(NOON, 'ymd-dot')).toBe('2024.01.20')
    expect(formatUserDay(NOON, 'ymd-jp')).toBe('2024年01月20日')
    expect(formatUserDay(NOON, undefined)).toBe('2024-01-20')
  })

  it('carries no clock in the locale format either', () => {
    // `locale` delegates to the BROWSER's locale, so there is no string to pin
    // - and comparing against `Intl.DateTimeFormat` with the same options would
    // be comparing the implementation with itself. What is left are two
    // properties, both stated so that no locale can fail them for the wrong
    // reason: `\p{Nd}` rather than `\d`, because `\d` is ASCII-only and would
    // read Arabic-Indic digits as no digits at all.
    const day = formatUserDay(NOON, 'locale')
    expect(day).toBeTruthy()
    // No clock. This is the property a trimmed stamp broke: some locales put
    // the clock FIRST (vi-VN), so cutting at the first space kept `13:00`.
    expect(day).not.toMatch(/\p{Nd}{1,2}:\p{Nd}{2}/u)
    // And a whole day rather than a fragment of one: three number groups, for
    // the year, the month and the day, whichever order and numeral system the
    // locale writes them in. Trimming left ONE (`2024.` in ko-KR, `20.` in
    // cs-CZ) or the clock's two, so this is what tells a day from a piece of
    // one without naming a single locale.
    expect(day.match(/\p{Nd}+/gu)).toHaveLength(3)
  })

  it('matches formatUserDate on the falsy and unparseable paths', () => {
    expect(formatUserDay('', 'iso')).toBe('')
    expect(formatUserDay(null, 'iso')).toBe('')
    expect(formatUserDay('not-a-date', 'iso')).toBe('not-a-date')
  })
})

describe('getStackThreshold', () => {
  it('returns 0.9 for null / undefined / empty', () => {
    expect(getStackThreshold(null)).toBe(0.9)
    expect(getStackThreshold(undefined)).toBe(0.9)
    expect(getStackThreshold('')).toBe(0.9)
  })

  it('returns 0.9 for non-positive values', () => {
    expect(getStackThreshold(0)).toBe(0.9)
    expect(getStackThreshold(-1)).toBe(0.9)
  })

  it('clamps to [0.5, 0.99999]', () => {
    expect(getStackThreshold(0.1)).toBe(0.5)
    expect(getStackThreshold(1.5)).toBe(0.99999)
    expect(getStackThreshold(0.75)).toBeCloseTo(0.75)
  })
})

describe('arraysEqualByString', () => {
  it('returns true for equal arrays', () => {
    expect(arraysEqualByString([1, 2, 3], [1, 2, 3])).toBe(true)
    expect(arraysEqualByString(['a', 'b'], ['a', 'b'])).toBe(true)
  })

  it('returns true when stringified values match', () => {
    expect(arraysEqualByString([1, 2], ['1', '2'])).toBe(true)
  })

  it('returns false for different lengths', () => {
    expect(arraysEqualByString([1], [1, 2])).toBe(false)
  })

  it('returns false for non-array inputs', () => {
    expect(arraysEqualByString(null, [1])).toBe(false)
    expect(arraysEqualByString([1], null)).toBe(false)
  })
})

describe('isRangeOverlap', () => {
  it('detects overlapping ranges', () => {
    expect(isRangeOverlap(0, 10, 5, 15)).toBe(true)
  })

  it('returns false for adjacent non-overlapping ranges', () => {
    expect(isRangeOverlap(0, 5, 5, 10)).toBe(false)
  })

  it('returns false for non-overlapping ranges', () => {
    expect(isRangeOverlap(0, 3, 5, 10)).toBe(false)
  })

  it('detects when one range is fully inside another', () => {
    expect(isRangeOverlap(0, 20, 5, 10)).toBe(true)
  })
})

describe('rangeCovers', () => {
  it('returns true when range fully covers the query', () => {
    expect(rangeCovers([[0, 100]], 10, 90)).toBe(true)
  })

  it('returns false when no range covers the query', () => {
    expect(rangeCovers([[0, 5]], 3, 10)).toBe(false)
  })

  it('requires both start and end to be within a single range', () => {
    expect(rangeCovers([[0, 5], [8, 15]], 4, 10)).toBe(false)
  })
})

describe('normalizePluginProgressMessage', () => {
  it('returns empty string for empty/falsy input', () => {
    expect(normalizePluginProgressMessage('', '')).toBe('')
    expect(normalizePluginProgressMessage(null, null)).toBe('')
  })

  it('uses fallback when message is empty', () => {
    expect(normalizePluginProgressMessage('', 'fallback msg')).toBe('fallback msg')
  })

  it('unwraps a JSON-quoted string', () => {
    expect(normalizePluginProgressMessage('"hello world"', '')).toBe('hello world')
  })

  it('normalises escaped newlines', () => {
    const result = normalizePluginProgressMessage('line1\\nline2', '')
    expect(result).toBe('line1\nline2')
  })

  it('passes plain strings through unchanged', () => {
    expect(normalizePluginProgressMessage('Processing image', '')).toBe('Processing image')
  })
})

describe('extractComfyuiExecutionErrorMessage', () => {
  it('extracts exception_message from execution_error payload', () => {
    const payload = {
      type: 'execution_error',
      data: {
        exception_message: 'Allocation on device 0 would exceed allowed memory. This error means you ran out of memory on your GPU.',
      },
    }
    const result = extractComfyuiExecutionErrorMessage(payload)
    expect(result).toContain('Allocation on device 0 would exceed allowed memory')
    expect(result).toContain('out of memory on your GPU')
  })

  it('falls back to node_errors message when present', () => {
    const payload = {
      type: 'execution_error',
      data: {
        node_errors: {
          '22': [
            {
              message: 'KSampler failed: CUDA out of memory',
            },
          ],
        },
      },
    }
    const result = extractComfyuiExecutionErrorMessage(payload)
    expect(result).toContain('KSampler failed: CUDA out of memory')
  })
})

describe('formatComfyuiExecutionErrorMessage', () => {
  it('prefixes extracted message with fallback label', () => {
    const payload = {
      type: 'execution_error',
      data: {
        exception_message: 'CUDA out of memory',
      },
    }
    expect(formatComfyuiExecutionErrorMessage(payload)).toBe('ComfyUI failed: CUDA out of memory')
  })
})

describe('isComfyuiOutOfMemoryMessage', () => {
  it('detects common OOM phrases', () => {
    expect(isComfyuiOutOfMemoryMessage('CUDA out of memory')).toBe(true)
    expect(isComfyuiOutOfMemoryMessage('Allocation on device 0 would exceed allowed memory')).toBe(true)
  })

  it('returns false for unrelated messages', () => {
    expect(isComfyuiOutOfMemoryMessage('Workflow missing placeholder {{image_path}}')).toBe(false)
  })
})

describe('getStackColorIndexFromId', () => {
  it('returns null for null / undefined', () => {
    expect(getStackColorIndexFromId(null)).toBeNull()
    expect(getStackColorIndexFromId(undefined)).toBeNull()
  })

  it('returns numeric id as-is for numeric input', () => {
    expect(getStackColorIndexFromId(7)).toBe(7)
    expect(getStackColorIndexFromId('42')).toBe(42)
  })

  it('returns a stable non-null hash for non-numeric string ids', () => {
    const a = getStackColorIndexFromId('abc')
    const b = getStackColorIndexFromId('abc')
    expect(a).toBe(b)
    expect(a).not.toBeNull()
  })
})

describe('applyStackBackgroundAlpha', () => {
  it('returns non-colour values unchanged', () => {
    expect(applyStackBackgroundAlpha('')).toBe('')
    expect(applyStackBackgroundAlpha(null)).toBeNull()
  })

  it('does not re-process already-alpha colours', () => {
    const hsla = 'hsla(220, 60%, 48%, 0.6)'
    expect(applyStackBackgroundAlpha(hsla)).toBe(hsla)
    const rgba = 'rgba(0, 128, 255, 0.6)'
    expect(applyStackBackgroundAlpha(rgba)).toBe(rgba)
  })

  it('adds alpha to hsl() (space-separated syntax)', () => {
    const result = applyStackBackgroundAlpha('hsl(220 60% 48%)')
    expect(result).toContain('0.6')
  })

  it('adds alpha to rgb()', () => {
    const result = applyStackBackgroundAlpha('rgb(0, 128, 255)')
    expect(result).toContain('0.6')
  })
})

/**
 * `hsl(H S% L%)` → `{r,g,b}`, so the tint can be measured with the same
 * contrast helpers the token audit uses. Only handles the space-separated
 * syntax applyStackBadgeTint emits.
 */
function hslToRgb(color) {
  const [h, s, l] = /^hsl\((\d+) (\d+)% (\d+)%\)$/.exec(color).slice(1).map(Number)
  const sat = s / 100
  const light = l / 100
  const a = sat * Math.min(light, 1 - light)
  const f = (n) => {
    const k = (n + h / 30) % 12
    return (light - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))) * 255
  }
  return { r: f(0), g: f(8), b: f(4) }
}

describe('applyStackBadgeTint', () => {
  it('keeps the hue and normalises saturation and lightness', () => {
    // The hue is the identity; everything else is field styling that does not
    // survive at 14px on a scrim.
    expect(applyStackBadgeTint('hsl(220 60% 48%)')).toBe('hsl(220 70% 72%)')
  })

  it('gives one stack one tint regardless of where its cover lands', () => {
    // getStackColor swings lightness and saturation on grid parity. As a field
    // that is invisible; as a glyph it would be the identity channel changing
    // for a reason the user cannot act on.
    const tints = [
      getStackColor(0, 0, 0),
      getStackColor(0, 1, 0),
      getStackColor(0, 0, 1),
      getStackColor(0, 1, 1),
    ].map(applyStackBadgeTint)
    expect(new Set(tints).size).toBe(1)
  })

  it('accepts the comma and deg spellings a server colour may arrive in', () => {
    expect(applyStackBadgeTint('hsl(145, 60%, 48%)')).toBe('hsl(145 70% 72%)')
    expect(applyStackBadgeTint('hsla(145deg 60% 48% / 0.6)')).toBe(
      'hsl(145 70% 72%)',
    )
  })

  it('returns null for anything it cannot read as a hue', () => {
    // The badge falls back to its known-legible glyph rather than painting an
    // unverified colour onto a photo.
    expect(applyStackBadgeTint('#3b82f6')).toBeNull()
    expect(applyStackBadgeTint('rgb(59, 130, 246)')).toBeNull()
    expect(applyStackBadgeTint('rebeccapurple')).toBeNull()
    expect(applyStackBadgeTint('')).toBeNull()
    expect(applyStackBadgeTint(null)).toBeNull()
    expect(applyStackBadgeTint(undefined)).toBeNull()
  })

  it('clears 3:1 on --scrim-photo-strong for every hue in the palette', () => {
    // This is the arithmetic the whole spec rests on, so it is asserted rather
    // than eyeballed (the same reasoning as contrastAudit.test.js). Worst case
    // is a WHITE photo under the chip, because a dark photo only helps a light
    // glyph. Floor is WCAG 1.4.11 non-text, 3:1: the glyph is a graphic, and
    // the count beside it carries the meaning in text.
    const chip = composite('#000000', '#ffffff', 0.78)
    for (let index = 0; index < 8; index += 1) {
      const tint = applyStackBadgeTint(getStackColor(index))
      expect(contrastRatio(hslToRgb(tint), chip)).toBeGreaterThanOrEqual(3)
    }
  })

  it('would NOT clear 3:1 on the ordinary photo scrim', () => {
    // Pins the pairing: the tint is only legible because
    // `.sbadge--tinted` deepens the chip to --scrim-photo-strong. Anyone who
    // drops that rule to "simplify" fails here instead of shipping an
    // indicator that vanishes on bright photos.
    const chip = composite('#000000', '#ffffff', 0.55)
    const worst = applyStackBadgeTint(getStackColor(7)) // hue 258, the darkest
    expect(contrastRatio(hslToRgb(worst), chip)).toBeLessThan(3)
  })
})

describe('debounce', () => {
  it('runs once on the trailing edge, with the last arguments', () => {
    vi.useFakeTimers()
    const spy = vi.fn()
    const debounced = debounce(spy, 100)

    debounced('a')
    debounced('b')
    debounced('c')
    expect(spy).not.toHaveBeenCalled()

    vi.advanceTimersByTime(99)
    expect(spy).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith('c')
    vi.useRealTimers()
  })

  it('cancel() drops the pending call', () => {
    vi.useFakeTimers()
    const spy = vi.fn()
    const debounced = debounce(spy, 100)

    debounced()
    debounced.cancel()
    vi.advanceTimersByTime(1000)
    expect(spy).not.toHaveBeenCalled()

    // cancel() must not poison the instance: it debounces again afterwards.
    debounced()
    vi.advanceTimersByTime(100)
    expect(spy).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})
