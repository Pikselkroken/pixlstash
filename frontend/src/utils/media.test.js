import { describe, it, expect } from 'vitest'
import {
  buildMediaUrl,
  isFaceDrag,
  isFileDrag,
  isInternalImageDrag,
  isPictureDrag,
  setInternalDragPayload,
  FACE_DRAG_MIME,
  PICTURE_DRAG_MIME,
} from './media.js'

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

// A drop target may only read `types` during dragover, so the payload kind has
// to be a key. Before this, a face drag and a picture drag were indistinguisable
// until the drop had already happened (issue #757).
describe('internal drag payload markers', () => {
  function writer() {
    const store = {}
    return {
      store,
      dataTransfer: {
        setData: (type, value) => {
          store[type] = value
        },
        get types() {
          return Object.keys(store)
        },
      },
    }
  }

  it('marks a picture payload so dragover can recognise it', () => {
    const { store, dataTransfer } = writer()
    setInternalDragPayload(dataTransfer, { type: 'image-ids', imageIds: [1] })

    expect(JSON.parse(store['application/json'])).toEqual({
      type: 'image-ids',
      imageIds: [1],
    })
    expect(isPictureDrag(dataTransfer)).toBe(true)
    expect(isFaceDrag(dataTransfer)).toBe(false)
    expect(isInternalImageDrag(dataTransfer)).toBe(true)
  })

  it('marks a face payload distinctly, despite it carrying imageIds too', () => {
    const { dataTransfer } = writer()
    setInternalDragPayload(dataTransfer, {
      type: 'face-bbox',
      faceIds: [9],
      imageIds: [1],
    })

    expect(isFaceDrag(dataTransfer)).toBe(true)
    expect(isPictureDrag(dataTransfer)).toBe(false)
    expect(isInternalImageDrag(dataTransfer)).toBe(true)
  })

  it('reports neither kind for an external file drag', () => {
    expect(isPictureDrag(dt({ types: ['Files'] }))).toBe(false)
    expect(isFaceDrag(dt({ types: ['Files'] }))).toBe(false)
    expect(isPictureDrag(null)).toBe(false)
    expect(isFaceDrag(null)).toBe(false)
  })

  it('keeps the two marker types apart', () => {
    expect(PICTURE_DRAG_MIME).not.toBe(FACE_DRAG_MIME)
  })
})

describe('buildMediaUrl', () => {
  it('builds an extension-qualified native-media URL', () => {
    expect(
      buildMediaUrl({
        backendUrl: '/api/v1',
        image: { id: 7, format: 'PNG', pixel_sha: 'abc' },
      }),
    ).toBe('/api/v1/pictures/7.png?v=abc')
  })

  it('does not turn an id-only placeholder into a JSON endpoint media URL', () => {
    expect(buildMediaUrl({ backendUrl: '/api/v1', image: { id: 7 } })).toBe('')
  })
})
