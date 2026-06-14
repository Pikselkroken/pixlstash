import { describe, it, expect } from 'vitest'
import { isFileDrag, isInternalImageDrag } from './media.js'

// Minimal DataTransfer stand-in: only `types` (array) and `files` (array-like)
// are read by the drag predicates.
function dt({ types = [], files = [] } = {}) {
  return { types, files }
}

describe('isInternalImageDrag', () => {
  it('is true when the drag carries our application/json payload', () => {
    expect(isInternalImageDrag(dt({ types: ['application/json'] }))).toBe(true)
  })

  it('is false for an external OS file drag', () => {
    expect(isInternalImageDrag(dt({ types: ['Files'], files: [{}] }))).toBe(false)
  })

  // Regression: on the Electron desktop shell, dragging an in-page thumbnail
  // onto a character/set populates dataTransfer.files with the image as a real
  // File *in addition to* our marker. The marker must still win so the window
  // import handler doesn't import the picture instead of assigning it.
  it('is true even when the desktop shell also attaches the image as a file', () => {
    expect(
      isInternalImageDrag(dt({ types: ['application/json', 'Files'], files: [{}] })),
    ).toBe(true)
  })

  it('is false for null/empty data transfer', () => {
    expect(isInternalImageDrag(null)).toBe(false)
    expect(isInternalImageDrag(dt())).toBe(false)
  })
})

describe('isFileDrag', () => {
  it('detects an external file drag by type', () => {
    expect(isFileDrag(dt({ types: ['Files'] }))).toBe(true)
    expect(isFileDrag(dt({ types: ['application/x-moz-file'] }))).toBe(true)
  })

  it('is false for an internal-only drag', () => {
    expect(isFileDrag(dt({ types: ['application/json'] }))).toBe(false)
  })
})
