// The rotate gates and copy, without a mounted view.
//
// Three surfaces read these (the lightbox toolbar, its `[` / `]` shortcuts and
// the grid context menu), so a gate that drifted would show a live control on
// one and a greyed one on another for the same picture. Testing them here is
// what makes "the same answer everywhere" a property of one function rather
// than a coincidence between three components.

import { describe, it, expect } from "vitest";
import {
  ROTATE_CCW,
  ROTATE_CW,
  canRotateInPlace,
  rotateBlockReason,
  rotateMenuLabel,
  rotateSkipNote,
  ROTATE_FORMAT_REASON,
  ROTATE_REFERENCE_FOLDER_REASON,
} from "./rotate";

describe("canRotateInPlace", () => {
  it("accepts the two formats every renderer agrees on", () => {
    for (const format of ["jpg", "jpeg", "png", "JPG", "PNG"]) {
      expect(canRotateInPlace({ id: 1, format }), format).toBe(true);
    }
  });

  it("refuses WebP, whose orientation tag browsers ignore", () => {
    // The load-bearing exclusion: the write works, so nothing upstream fails -
    // the thumbnail would come out rotated and the full view would not.
    expect(canRotateInPlace({ id: 1, format: "webp" })).toBe(false);
  });

  it("refuses the other still formats and video", () => {
    for (const format of ["tiff", "tif", "bmp", "gif", "mp4", "webm", "mov"]) {
      expect(canRotateInPlace({ id: 1, format }), format).toBe(false);
    }
  });

  it("refuses a reference-folder file whatever its format", () => {
    // Files the user manages outside the library are never written to.
    expect(
      canRotateInPlace({ id: 1, format: "jpg", reference_folder_id: 4 }),
    ).toBe(false);
  });

  it("refuses a picture with nothing to go on", () => {
    expect(canRotateInPlace(null)).toBe(false);
    expect(canRotateInPlace({ id: 1 })).toBe(false);
  });
});

describe("rotateBlockReason", () => {
  it("stays live when one picture in a mixed selection can rotate", () => {
    // Over-blocking is its own regression: the selection has work to do, and
    // the receipt reports whatever the server left alone.
    expect(
      rotateBlockReason([
        { id: 1, format: "webp" },
        { id: 2, format: "jpg" },
        { id: 3, format: "mp4" },
      ]),
    ).toBeNull();
  });

  it("names the format when nothing in the selection can rotate", () => {
    expect(
      rotateBlockReason([
        { id: 1, format: "webp" },
        { id: 2, format: "gif" },
      ]),
    ).toBe(ROTATE_FORMAT_REASON);
  });

  it("names the reference folder when that is the only refusal", () => {
    expect(
      rotateBlockReason([{ id: 1, format: "jpg", reference_folder_id: 2 }]),
    ).toBe(ROTATE_REFERENCE_FOLDER_REASON);
  });

  it("falls back to the format sentence for a mixed refusal", () => {
    // The user can act on "pick different pictures"; they cannot act on a
    // sentence that names only one of the two reasons they are blocked.
    expect(
      rotateBlockReason([
        { id: 1, format: "jpg", reference_folder_id: 2 },
        { id: 2, format: "webp" },
      ]),
    ).toBe(ROTATE_FORMAT_REASON);
  });

  it("says nothing about an empty selection", () => {
    expect(rotateBlockReason([])).toBeNull();
    expect(rotateBlockReason(null)).toBeNull();
  });

  it("points every refusal at the route that still works", () => {
    expect(ROTATE_FORMAT_REASON).toContain("Filters > Rotate");
    expect(ROTATE_REFERENCE_FOLDER_REASON).toContain("Filters > Rotate");
  });
});

describe("rotateMenuLabel", () => {
  it("drops the count for a single picture", () => {
    expect(rotateMenuLabel(ROTATE_CCW, 1)).toBe("Rotate left");
    expect(rotateMenuLabel(ROTATE_CW, 1)).toBe("Rotate right");
  });

  it("carries the count over a multi-selection", () => {
    expect(rotateMenuLabel(ROTATE_CCW, 12)).toBe("Rotate 12 photos left");
    expect(rotateMenuLabel(ROTATE_CW, 12)).toBe("Rotate 12 photos right");
  });

  it("groups the numeral like every other count in the product", () => {
    expect(rotateMenuLabel(ROTATE_CW, 2700)).toBe("Rotate 2,700 photos right");
  });
});

describe("rotateSkipNote", () => {
  it("says nothing when everything asked for turned", () => {
    expect(rotateSkipNote({ rotated_picture_ids: [1, 2] })).toBe("");
    expect(rotateSkipNote(null)).toBe("");
  });

  it("counts the unsupported and points at the copy route", () => {
    const note = rotateSkipNote({
      rotated_picture_ids: [1],
      unsupported_picture_ids: [2, 3],
    });
    expect(note).toContain("2 left as they are");
    expect(note).toContain("Filters > Rotate");
  });

  it("keeps the two buckets apart", () => {
    // A format refusal has a route; a locked set has a different fix. Merging
    // them into one number would send half the pictures to the wrong remedy.
    const note = rotateSkipNote({
      rotated_picture_ids: [1],
      unsupported_picture_ids: [2],
      skipped_picture_ids: [3, 4],
    });
    expect(note).toContain("1 left as it is");
    expect(note).toContain("2 skipped");
  });
});
