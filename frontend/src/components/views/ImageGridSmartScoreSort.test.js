// Grid smart-score sort — null placement must match the server's ORDER BY.
//
// ImageGrid.vue (~7.4k lines) is impractical to mount, so this test exercises
// the exact contract the grid's incremental insert relies on: `gridImageSortKey`
// feeds a comparator (`descending ? otherKey < key : otherKey > key`) that finds
// where a freshly-fetched card belongs. The backend sorts the smart_score column
// with a plain .asc()/.desc() (pixlstash/db_models/picture.py), so SQLite's
// native NULL rule applies — NULL is less than every real value, hence NULLs
// FIRST on ascending and LAST on descending. The client must place a
// null-scored card in the same slot, or "a card lands in a different spot than
// the server put it" (the exact bug the `gridSortDescending` comment warns of).
//
// These helpers reproduce the grid's logic verbatim; keep them in sync with
// ImageGrid.vue's `gridImageSortKey` / `insertGridImagesById`.

import { describe, it, expect } from "vitest";

// Verbatim copy of ImageGrid.vue's getGridSmartScoreValue.
function getGridSmartScoreValue(img) {
  if (!img) return null;
  const raw =
    typeof img.smartScore === "number"
      ? img.smartScore
      : typeof img.smart_score === "number"
        ? img.smart_score
        : null;
  return Number.isFinite(raw) ? raw : null;
}

// Verbatim copy of the SMART_SCORE branch of ImageGrid.vue's gridImageSortKey.
function smartScoreSortKey(img) {
  const smart = getGridSmartScoreValue(img);
  return smart === null ? -Infinity : smart;
}

// Verbatim copy of insertGridImagesById's insert loop (smart-score sort).
function insertByServerOrder(base, pic, descending) {
  const key = smartScoreSortKey(pic);
  let insertIndex = base.findIndex((img) => {
    const otherKey = smartScoreSortKey(img);
    return descending ? otherKey < key : otherKey > key;
  });
  if (insertIndex === -1) insertIndex = base.length;
  const next = base.slice();
  next.splice(insertIndex, 0, pic);
  return next;
}

// What the server (SQLite ORDER BY smart_score, NULLs-as-smallest) would return.
function serverOrder(images, descending) {
  const NULL_KEY = -Infinity; // NULL < every real value in SQLite.
  return images
    .map((img, i) => ({ img, i }))
    .sort((a, b) => {
      const ka = getGridSmartScoreValue(a.img) ?? NULL_KEY;
      const kb = getGridSmartScoreValue(b.img) ?? NULL_KEY;
      if (ka !== kb) return descending ? kb - ka : ka - kb;
      // id tiebreak, matching the backend's cls.id.desc()/asc().
      return descending ? b.img.id - a.img.id : a.img.id - b.img.id;
    })
    .map((e) => e.img);
}

const ids = (arr) => arr.map((img) => img.id);

describe("grid smart-score sort — null placement matches the server", () => {
  // Real scores span negative → positive so a 0 sentinel would demonstrably
  // mis-order the null card; -Infinity keeps it consistent with SQLite.
  const withScores = [
    { id: 1, smart_score: 0.9 },
    { id: 2, smart_score: 0.1 },
    { id: 3, smart_score: -0.4 },
    { id: 4, smart_score: 0.0 },
  ];
  const nullCard = { id: 5, smart_score: null };

  it("sort key maps a null smart score below every real value", () => {
    expect(smartScoreSortKey(nullCard)).toBe(-Infinity);
    expect(smartScoreSortKey({ id: 9, smart_score: -100 })).toBeGreaterThan(
      smartScoreSortKey(nullCard),
    );
  });

  it("descending: a null-scored card inserts LAST, like the server", () => {
    const descending = true;
    const base = serverOrder(withScores, descending);
    const inserted = insertByServerOrder(base, nullCard, descending);
    const expected = serverOrder([...withScores, nullCard], descending);
    expect(ids(inserted)).toEqual(ids(expected));
    expect(ids(inserted)[ids(inserted).length - 1]).toBe(nullCard.id);
  });

  it("ascending: a null-scored card inserts FIRST, like the server", () => {
    const descending = false;
    const base = serverOrder(withScores, descending);
    const inserted = insertByServerOrder(base, nullCard, descending);
    const expected = serverOrder([...withScores, nullCard], descending);
    expect(ids(inserted)).toEqual(ids(expected));
    expect(ids(inserted)[0]).toBe(nullCard.id);
  });

  it("a real card still inserts at its ranked slot in both directions", () => {
    const base = [nullCard, ...withScores];
    for (const descending of [true, false]) {
      const sortedBase = serverOrder(base, descending);
      const newCard = { id: 6, smart_score: 0.5 };
      const inserted = insertByServerOrder(sortedBase, newCard, descending);
      const expected = serverOrder([...base, newCard], descending);
      expect(ids(inserted)).toEqual(ids(expected));
    }
  });
});
