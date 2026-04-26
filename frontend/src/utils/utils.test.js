import { describe, it, expect } from 'vitest'
import {
  toggleScore,
  formatUserDate,
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
} from './utils.js'

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
