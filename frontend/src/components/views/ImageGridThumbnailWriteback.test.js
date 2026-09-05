// Grid thumbnail batch - write results back by picture identity, not by slot.
//
// Symptom this guards: you add a penalised tag, the card moves to its new
// smart-score position correctly, but it never shows the problem indicator.
//
// fetchThumbnailsBatch snapshots `allGridImages.slice(start, end)` and then awaits
// the network. `penalised_tags` (what drives the indicator) only refreshes through
// that response. Meanwhile repositionImageBySmartScore → _spliceAndReinsert
// re-orders allGridImages *without* bumping thumbnailRequestEpoch, so the epoch
// guard in the batch cannot see the move. A positional write-back
// (`allGridImages[start + i] = img`) then lands the refreshed payload on whichever
// picture now occupies that slot: the tagged card keeps its stale tags (no
// indicator) and an unrelated card is corrupted with someone else's data.
//
// ImageGrid.vue (~7.4k lines) is impractical to mount, so this reproduces the
// write-back verbatim. Keep in sync with ImageGrid.vue's fetchThumbnailsBatch.

import { describe, it, expect } from "vitest";

// Verbatim copy of the write-back loop, with `byIdentity` toggling the fix so the
// test can prove the positional version is genuinely broken.
function writeBack(allGridImages, gridImages, start, byIdentity) {
  const movedIndexById = new Map();
  if (byIdentity) {
    for (let i = 0; i < allGridImages.length; i += 1) {
      const existingId = allGridImages[i]?.id;
      if (existingId == null) continue;
      const key = String(existingId);
      if (!movedIndexById.has(key)) movedIndexById.set(key, i);
    }
  }
  for (let i = 0; i < gridImages.length; i++) {
    const img = gridImages[i];
    if (img.id == null) continue;
    let targetIndex = start + i;
    if (byIdentity) {
      if (String(allGridImages[targetIndex]?.id) !== String(img.id)) {
        const moved = movedIndexById.get(String(img.id));
        if (moved === undefined) continue;
        targetIndex = moved;
      }
    }
    img.idx = targetIndex;
    allGridImages[targetIndex] = img;
  }
  return allGridImages;
}

// The grid as it looked when the batch was snapshotted.
function initialGrid() {
  return [
    { id: 1, penalised_tags: [] },
    { id: 2, penalised_tags: [] },
    { id: 3, penalised_tags: [] },
  ];
}

// The batch response: picture 2 came back carrying a freshly-applied problem tag.
function batchResponse() {
  return [
    { id: 1, penalised_tags: [] },
    { id: 2, penalised_tags: ["bad anatomy"] },
    { id: 3, penalised_tags: [] },
  ];
}

// Picture 2 gained a penalised tag, so its smart score dropped and the card was
// repositioned to the end while the batch was still in flight.
function gridAfterReposition() {
  return [
    { id: 1, penalised_tags: [] },
    { id: 3, penalised_tags: [] },
    { id: 2, penalised_tags: [] },
  ];
}

describe("thumbnail batch write-back", () => {
  it("lands the problem indicator on the card that moved", () => {
    const result = writeBack(
      gridAfterReposition(),
      batchResponse(),
      0,
      true,
    );
    const byId = Object.fromEntries(result.map((img) => [img.id, img]));
    expect(byId[2].penalised_tags).toEqual(["bad anatomy"]);
    // And nobody else was given picture 2's tags.
    expect(byId[1].penalised_tags).toEqual([]);
    expect(byId[3].penalised_tags).toEqual([]);
  });

  it("keeps positional behaviour when nothing moved", () => {
    const result = writeBack(initialGrid(), batchResponse(), 0, true);
    expect(result.map((img) => img.id)).toEqual([1, 2, 3]);
    expect(result[1].penalised_tags).toEqual(["bad anatomy"]);
    expect(result.map((img) => img.idx)).toEqual([0, 1, 2]);
  });

  it("positional write-back silently undoes the reposition", () => {
    const before = gridAfterReposition().map((img) => img.id);
    expect(before).toEqual([1, 3, 2]);

    const result = writeBack(
      gridAfterReposition(),
      batchResponse(),
      0,
      false,
    );

    // Every slot in the window is overwritten from the pre-move snapshot, so the
    // grid reverts to the order the batch was built against and the card the user
    // just tagged jumps back to where it was. Writing by identity preserves the
    // move (asserted above).
    expect(result.map((img) => img.id)).toEqual([1, 2, 3]);
  });
});
