import { describe, it, expect, vi } from 'vitest'
import {
  buildMediaUrl,
  displayedAspectRatio,
  isFaceDrag,
  isFileDrag,
  isInternalImageDrag,
  isPictureDrag,
  setInternalDragPayload,
  isSupportedImportFile,
  ARCHIVE_EXTENSIONS,
  CAPTION_EXTENSIONS,
  FACE_DRAG_MIME,
  IMPORT_FILE_ACCEPT,
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
// to be a key. Before this, a face drag and a picture drag were indistinguishable
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

  it('leaves an unmapped payload kind unmarked rather than calling it a picture',
     () => {
       const {store, dataTransfer} = writer()
       const complained =
           vi.spyOn(console, 'error').mockImplementation(() => {})

       setInternalDragPayload(dataTransfer, {type: 'something-new', ids: [1]})

       expect(JSON.parse(store['application/json']).type).toBe('something-new')
       expect(isPictureDrag(dataTransfer)).toBe(false)
       expect(isFaceDrag(dataTransfer)).toBe(false)
       expect(complained).toHaveBeenCalled()
       complained.mockRestore()
     })
})

describe('buildMediaUrl', () => {
  it('builds an extension-qualified native-media URL', () => {
    expect(
      buildMediaUrl({
        backendUrl: '/api/v1',
        image: { id: 7, format: 'PNG', pixel_sha: 'abc' },
      }),
    ).toBe('/api/v1/pictures/7.png')
  })

  // The buster is the EXIF orientation, not the content hash: an in-place
  // rotate copies every pixel through, so the hash cannot express the one edit
  // that needs busting - and `orientation` is in the grid projection, so the
  // lightbox and both full-image preloaders build the same URL from the same
  // record. A picture that has never been turned keeps its bare URL.
  it('busts the URL on the orientation, not the content hash', () => {
    expect(
      buildMediaUrl({
        backendUrl: '/api/v1',
        image: { id: 7, format: 'PNG', pixel_sha: 'abc', orientation: 6 },
      }),
    ).toBe('/api/v1/pictures/7.png?v=o6')
    expect(
      buildMediaUrl({
        backendUrl: '/api/v1',
        image: { id: 7, format: 'PNG', orientation: 1 },
      }),
    ).toBe('/api/v1/pictures/7.png')
  })

  it('does not turn an id-only placeholder into a JSON endpoint media URL', () => {
    expect(buildMediaUrl({ backendUrl: '/api/v1', image: { id: 7 } })).toBe('')
  })
})

describe('displayedAspectRatio', () => {
  it('takes the thumbnail bitmap as-is - it is already EXIF-transposed', () => {
    expect(
      displayedAspectRatio({
        thumbnail_width: 300,
        thumbnail_height: 200,
        width: 3000,
        height: 2000,
        orientation: 6,
      }),
    ).toBeCloseTo(1.5)
  })

  // The window an in-place rotate opens: apply_orientation NULLs the thumbnail
  // dimensions to re-queue the bitmap, so a turned card sits on the RAW
  // width/height - which do not swap - until the sweep lands. Unswapped here,
  // the tile keeps its pre-rotate shape and then jumps.
  it('swaps the raw dimensions for a quarter-turned picture', () => {
    expect(displayedAspectRatio({ width: 4000, height: 3000 })).toBeCloseTo(4 / 3)
    expect(
      displayedAspectRatio({ width: 4000, height: 3000, orientation: 1 }),
    ).toBeCloseTo(4 / 3)
    // 3 and 2 are the 180deg / mirrored-horizontal cases: still landscape.
    expect(
      displayedAspectRatio({ width: 4000, height: 3000, orientation: 3 }),
    ).toBeCloseTo(4 / 3)
    for (const orientation of [5, 6, 7, 8]) {
      expect(
        displayedAspectRatio({ width: 4000, height: 3000, orientation }),
      ).toBeCloseTo(3 / 4)
    }
  })

  it('falls back to square rather than dividing by zero', () => {
    expect(displayedAspectRatio(null)).toBe(1)
    expect(displayedAspectRatio({ width: 0, height: 0 })).toBe(1)
  })
})

describe('IMPORT_FILE_ACCEPT', () => {
  // The empty-library card shipped with `image/*,video/*` and nothing else, so
  // a zip or a caption file could only be reached through the picker's "All
  // Files" - an import route the app supports and its own dialog hid. These
  // assert the offer against the predicate rather than against a copy of the
  // string, so the two cannot drift apart silently again.

  it('offers every file extension the importer will actually take', () => {
    const offered = IMPORT_FILE_ACCEPT.split(',')
      .filter((entry) => entry.startsWith('.'))
      .map((entry) => entry.slice(1))

    expect(offered.length).toBeGreaterThan(0)
    for (const ext of offered) {
      expect(isSupportedImportFile({ name: `sample.${ext}` })).toBe(true)
    }
  })

  it('advertises every type the importer takes, the other direction', () => {
    // The check above is a subset check: it would stay green if a supported
    // type were dropped from the offer, which is the drift that actually
    // matters. So walk the predicate's own lists instead.
    //
    // Media is carried by the two wildcards rather than named extension by
    // extension, so those are asserted outright - removing either would hide
    // every picture and every video while leaving the subset check green.
    expect(IMPORT_FILE_ACCEPT).toContain('image/*')
    expect(IMPORT_FILE_ACCEPT).toContain('video/*')

    // Everything else matches neither wildcard and has to be named.
    for (const ext of [...ARCHIVE_EXTENSIONS, ...CAPTION_EXTENSIONS]) {
      expect(isSupportedImportFile({ name: `shoot.${ext}` })).toBe(true)
      expect(IMPORT_FILE_ACCEPT).toContain(`.${ext}`)
    }
  })
})
