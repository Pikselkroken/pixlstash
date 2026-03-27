import { describe, it, expect } from 'vitest'
import {
  getPictureStackId,
  normalizeStackIdValue,
  getStackPositionValue,
  getStackSmartScoreValue,
  compareStackOrder,
  sortStackMembers,
  selectNewestStackMember,
  buildStackLeaderMap,
  getStackBadgeCount,
  shouldShowStackBadge,
  stackBadgeTitle,
} from './stack.js'

describe('getPictureStackId', () => {
  it('returns stringified stack_id', () => {
    expect(getPictureStackId({ stack_id: 5 })).toBe('5')
  })

  it('falls back to stackId camelCase', () => {
    expect(getPictureStackId({ stackId: 3 })).toBe('3')
  })

  it('returns null when no stack id is present', () => {
    expect(getPictureStackId({})).toBeNull()
    expect(getPictureStackId(null)).toBeNull()
  })
})

describe('normalizeStackIdValue', () => {
  it('converts numeric-looking strings to numbers', () => {
    expect(normalizeStackIdValue('7')).toBe(7)
  })

  it('keeps non-numeric strings as strings', () => {
    expect(normalizeStackIdValue('abc')).toBe('abc')
  })

  it('returns null for null / undefined', () => {
    expect(normalizeStackIdValue(null)).toBeNull()
    expect(normalizeStackIdValue(undefined)).toBeNull()
  })
})

describe('getStackPositionValue', () => {
  it('returns numeric position from stack_position', () => {
    expect(getStackPositionValue({ stack_position: 2 })).toBe(2)
  })

  it('falls back to stackPosition camelCase', () => {
    expect(getStackPositionValue({ stackPosition: 1 })).toBe(1)
  })

  it('returns null for missing or non-finite values', () => {
    expect(getStackPositionValue({})).toBeNull()
    expect(getStackPositionValue({ stack_position: 'x' })).toBeNull()
    expect(getStackPositionValue(null)).toBeNull()
  })
})

describe('getStackSmartScoreValue', () => {
  it('returns smartScore when present', () => {
    expect(getStackSmartScoreValue({ smartScore: 0.8 })).toBeCloseTo(0.8)
  })

  it('falls back to smart_score snake_case', () => {
    expect(getStackSmartScoreValue({ smart_score: 0.5 })).toBeCloseTo(0.5)
  })

  it('returns 0 for missing or non-finite values', () => {
    expect(getStackSmartScoreValue({})).toBe(0)
    expect(getStackSmartScoreValue({ smartScore: NaN })).toBe(0)
  })
})

describe('compareStackOrder', () => {
  it('sorts by explicit stack_position first (ascending)', () => {
    const a = { stack_position: 1, score: 5 }
    const b = { stack_position: 0, score: 5 }
    expect(compareStackOrder(a, b)).toBeGreaterThan(0)
    expect(compareStackOrder(b, a)).toBeLessThan(0)
  })

  it('pushes items without position after those with position', () => {
    const withPos = { stack_position: 0 }
    const noPos = {}
    expect(compareStackOrder(noPos, withPos)).toBeGreaterThan(0)
  })

  it('falls back to score (descending) when positions are equal/absent', () => {
    const high = { score: 9 }
    const low = { score: 3 }
    expect(compareStackOrder(high, low)).toBeLessThan(0)
    expect(compareStackOrder(low, high)).toBeGreaterThan(0)
  })

  it('falls back to id (ascending) as final tiebreaker', () => {
    const a = { id: 10, score: 5 }
    const b = { id: 5, score: 5 }
    expect(compareStackOrder(a, b)).toBeGreaterThan(0)
  })
})

describe('sortStackMembers', () => {
  it('returns an empty array for non-array input', () => {
    expect(sortStackMembers(null)).toEqual([])
  })

  it('does not mutate the original array', () => {
    const members = [
      { id: 2, score: 3, stack_position: 1 },
      { id: 1, score: 3, stack_position: 0 },
    ]
    const original = [...members]
    sortStackMembers(members)
    expect(members).toEqual(original)
  })

  it('orders by stack_position ascending', () => {
    const members = [
      { id: 1, score: 5, stack_position: 2 },
      { id: 2, score: 5, stack_position: 0 },
      { id: 3, score: 5, stack_position: 1 },
    ]
    const sorted = sortStackMembers(members)
    expect(sorted.map((m) => m.id)).toEqual([2, 3, 1])
  })
})

describe('selectNewestStackMember', () => {
  it('returns null for empty/non-array input', () => {
    expect(selectNewestStackMember([])).toBeNull()
    expect(selectNewestStackMember(null)).toBeNull()
  })

  it('returns the member with the most recent created_at', () => {
    const members = [
      { id: 1, created_at: '2023-01-01T00:00:00Z' },
      { id: 2, created_at: '2024-06-01T00:00:00Z' },
      { id: 3, created_at: '2022-01-01T00:00:00Z' },
    ]
    expect(selectNewestStackMember(members).id).toBe(2)
  })

  it('selects the highest id when created_at timestamps are equal', () => {
    const ts = '2024-01-01T00:00:00Z'
    const members = [
      { id: 3, created_at: ts },
      { id: 7, created_at: ts },
      { id: 5, created_at: ts },
    ]
    expect(selectNewestStackMember(members).id).toBe(7)
  })
})

describe('buildStackLeaderMap', () => {
  it('returns a map from stackId to leading image id', () => {
    const images = [
      { id: 10, stack_id: 1, score: 9, stack_position: 0 },
      { id: 11, stack_id: 1, score: 3, stack_position: 1 },
      { id: 20, stack_id: 2, score: 7, stack_position: 0 },
    ]
    const leaders = buildStackLeaderMap(images)
    expect(leaders.get('1')).toBe('10')
    expect(leaders.get('2')).toBe('20')
  })

  it('ignores images with no stack id', () => {
    const images = [{ id: 5 }, { id: 10, stack_id: 3, stack_position: 0 }]
    const leaders = buildStackLeaderMap(images)
    expect(leaders.size).toBe(1)
  })
})

describe('getStackBadgeCount', () => {
  it('returns stackCount when present', () => {
    expect(getStackBadgeCount({ stackCount: 4 })).toBe(4)
  })

  it('falls back to stack_count snake_case', () => {
    expect(getStackBadgeCount({ stack_count: 3 })).toBe(3)
  })

  it('returns 0 when absent or non-finite', () => {
    expect(getStackBadgeCount({})).toBe(0)
    expect(getStackBadgeCount(null)).toBe(0)
  })
})

describe('shouldShowStackBadge', () => {
  it('returns true only when count > 1', () => {
    expect(shouldShowStackBadge({ stackCount: 2 })).toBe(true)
    expect(shouldShowStackBadge({ stackCount: 1 })).toBe(false)
    expect(shouldShowStackBadge({ stackCount: 0 })).toBe(false)
  })
})

describe('stackBadgeTitle', () => {
  it('includes the count in the title string', () => {
    const title = stackBadgeTitle({ stackCount: 5 })
    expect(title).toContain('5')
  })

  it('returns empty string when count is <= 1', () => {
    expect(stackBadgeTitle({ stackCount: 1 })).toBe('')
    expect(stackBadgeTitle({})).toBe('')
  })
})
