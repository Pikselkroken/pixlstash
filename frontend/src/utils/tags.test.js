import { describe, it, expect } from 'vitest'
import {
  getTagLabel,
  getTagId,
  TagItem,
  getTagList,
  dedupeTagList,
  tagMatches,
  hasPenalisedTags,
  penalisedTagsTitle,
  penalisedTagIcon,
  penalisedTagColor,
} from './tags.js'

describe('getTagLabel', () => {
  it('returns a string tag unchanged', () => {
    expect(getTagLabel('landscape')).toBe('landscape')
  })

  it('extracts label from a tag object', () => {
    expect(getTagLabel({ id: 1, tag: 'portrait' })).toBe('portrait')
  })

  it('returns empty string for null / unexpected types', () => {
    expect(getTagLabel(null)).toBe('')
    expect(getTagLabel(42)).toBe('')
  })
})

describe('getTagId', () => {
  it('returns id from a tag object', () => {
    expect(getTagId({ id: 5, tag: 'sunset' })).toBe(5)
  })

  it('returns null when id is absent', () => {
    expect(getTagId({ tag: 'sunset' })).toBeNull()
    expect(getTagId('sunset')).toBeNull()
  })
})

describe('TagItem', () => {
  it('creates a tag item from a string', () => {
    expect(TagItem('cats')).toEqual({ id: null, tag: 'cats' })
  })

  it('creates a tag item from an object', () => {
    expect(TagItem({ id: 3, tag: 'dogs' })).toEqual({ id: 3, tag: 'dogs' })
  })

  it('returns null for empty or whitespace-only labels', () => {
    expect(TagItem('')).toBeNull()
    expect(TagItem('   ')).toBeNull()
  })

  it('trims whitespace from the label', () => {
    expect(TagItem('  cats  ')).toEqual({ id: null, tag: 'cats' })
  })
})

describe('getTagList', () => {
  it('maps mixed input to normalized tag items', () => {
    const result = getTagList(['cats', { id: 2, tag: 'dogs' }])
    expect(result).toEqual([
      { id: null, tag: 'cats' },
      { id: 2, tag: 'dogs' },
    ])
  })

  it('filters out null results (empty strings)', () => {
    expect(getTagList(['', 'valid'])).toEqual([{ id: null, tag: 'valid' }])
  })

  it('returns empty array for non-array input', () => {
    expect(getTagList(null)).toEqual([])
    expect(getTagList(undefined)).toEqual([])
  })
})

describe('dedupeTagList', () => {
  it('removes duplicate tags (case-insensitive)', () => {
    const input = [
      { id: null, tag: 'Cats' },
      { id: null, tag: 'cats' },
    ]
    expect(dedupeTagList(input)).toHaveLength(1)
  })

  it('prefers the entry with an id when deduplicating', () => {
    const input = [
      { id: null, tag: 'cats' },
      { id: 7, tag: 'cats' },
    ]
    const result = dedupeTagList(input)
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe(7)
  })

  it('returns results sorted alphabetically', () => {
    const input = [
      { id: null, tag: 'zebra' },
      { id: null, tag: 'apple' },
      { id: null, tag: 'mango' },
    ]
    const labels = dedupeTagList(input).map((t) => t.tag)
    expect(labels).toEqual(['apple', 'mango', 'zebra'])
  })

  it('filters out entries with no valid tag', () => {
    const input = [{ id: null, tag: '' }, { id: 1, tag: 'valid' }]
    expect(dedupeTagList(input)).toHaveLength(1)
  })
})

describe('tagMatches', () => {
  it('matches by id when both ids are set', () => {
    expect(tagMatches({ id: 3, tag: 'x' }, { id: 3, tag: 'y' })).toBe(true)
    expect(tagMatches({ id: 3, tag: 'x' }, { id: 4, tag: 'x' })).toBe(false)
  })

  it('falls back to tag string match when ids are absent', () => {
    expect(tagMatches({ tag: 'cats' }, { tag: 'cats' })).toBe(true)
    expect(tagMatches({ tag: 'cats' }, { tag: 'dogs' })).toBe(false)
  })

  it('matches against a plain string target', () => {
    expect(tagMatches({ tag: 'cats' }, 'cats')).toBe(true)
    expect(tagMatches({ tag: 'cats' }, 'dogs')).toBe(false)
  })

  it('returns false for null tag', () => {
    expect(tagMatches(null, 'cats')).toBe(false)
  })
})

describe('hasPenalisedTags', () => {
  it('returns true when penalised_tags is a non-empty array', () => {
    expect(hasPenalisedTags({ penalised_tags: ['blur'] })).toBe(true)
  })

  it('returns false when penalised_tags is empty or absent', () => {
    expect(hasPenalisedTags({ penalised_tags: [] })).toBe(false)
    expect(hasPenalisedTags({})).toBe(false)
    expect(hasPenalisedTags(null)).toBe(false)
  })
})

describe('penalisedTagsTitle', () => {
  it('lists penalised tags in the title', () => {
    const result = penalisedTagsTitle({ penalised_tags: ['blur', 'noise'] })
    expect(result).toContain('blur')
    expect(result).toContain('noise')
  })

  it('returns empty string when there are no penalised tags', () => {
    expect(penalisedTagsTitle({ penalised_tags: [] })).toBe('')
    expect(penalisedTagsTitle({})).toBe('')
  })
})

const W_LOW = { blur: 2, noise: 1 };        // blur→2, blur+noise→3, both mild (<7)
const W_MED = { blur: 4, noise: 3 };        // blur→4 (mild), blur+noise→7 (moderate)
const W_HIGH = { blur: 5, noise: 5 };       // blur→5 (mild), blur+noise→10 (moderate), blur+noise+x→15 (severe)
const W_HEAVY = { blur: 5, noise: 4, grain: 4 }; // blur+noise+grain→13 (severe, >12)

describe('penalisedTagIcon', () => {
  it('returns neutral icon for low total weight (< 7)', () => {
    // 1 tag, weight 2 → total 2 → mild
    expect(penalisedTagIcon({ penalised_tags: ['blur'] }, W_LOW)).toBe('mdi-emoticon-neutral-outline')
    // 1 tag, weight 4 → total 4 → mild
    expect(penalisedTagIcon({ penalised_tags: ['blur'] }, W_MED)).toBe('mdi-emoticon-neutral-outline')
    // 2 tags, weights 2+1=3 → mild
    expect(penalisedTagIcon({ penalised_tags: ['blur', 'noise'] }, W_LOW)).toBe('mdi-emoticon-neutral-outline')
  })

  it('returns sad icon for moderate total weight (7-12)', () => {
    // 2 tags, weights 4+3=7 → moderate
    expect(penalisedTagIcon({ penalised_tags: ['blur', 'noise'] }, W_MED)).toBe('mdi-emoticon-sad-outline')
    // 2 tags, weights 5+5=10 → moderate
    expect(penalisedTagIcon({ penalised_tags: ['blur', 'noise'] }, W_HIGH)).toBe('mdi-emoticon-sad-outline')
  })

  it('returns angry icon for high total weight (> 12)', () => {
    // 3 tags, weights 5+4+4=13 → severe
    expect(penalisedTagIcon({ penalised_tags: ['blur', 'noise', 'grain'] }, W_HEAVY, true)).toBe('mdi-emoticon-angry-outline')
  })

  it('falls back to count=1-per-tag when no weights map given', () => {
    // 6 tags, no weights → total 6 → mild
    expect(penalisedTagIcon({ penalised_tags: ['a', 'b', 'c', 'd', 'e', 'f'] })).toBe('mdi-emoticon-neutral')
    // 7 tags, no weights → total 7 → moderate
    expect(penalisedTagIcon({ penalised_tags: ['a', 'b', 'c', 'd', 'e', 'f', 'g'] })).toBe('mdi-emoticon-sad')
    // 13 tags, no weights → total 13 → severe
    expect(penalisedTagIcon({ penalised_tags: ['a','b','c','d','e','f','g','h','i','j','k','l','m'] })).toBe('mdi-emoticon-angry')
  })

  it('falls back to neutral for empty/null', () => {
    expect(penalisedTagIcon({})).toBe('mdi-emoticon-neutral')
    expect(penalisedTagIcon(null)).toBe('mdi-emoticon-neutral')
  })
})

describe('penalisedTagColor', () => {
  it('returns yellow for low total weight (< 7)', () => {
    expect(penalisedTagColor({ penalised_tags: ['blur'] }, W_MED)).toBe('#f9a825')
  })

  it('returns orange for moderate total weight (7-12)', () => {
    expect(penalisedTagColor({ penalised_tags: ['blur', 'noise'] }, W_MED)).toBe('#e65100')
  })

  it('returns deep red for high total weight (> 12)', () => {
    expect(penalisedTagColor({ penalised_tags: ['blur', 'noise', 'grain'] }, W_HEAVY)).toBe('#c62828')
  })
})
