// Grid smart-score sort - null placement must match the server's ORDER BY.
//
// ImageGrid.vue (~7.4k lines) is impractical to mount, so this test exercises
// the exact contract the grid's incremental insert relies on: `gridImageSortKey`
// feeds a comparator (`descending ? otherKey < key : otherKey > key`) that finds
// where a freshly-fetched card belongs. The backend sorts the smart_score column
// with a plain .asc()/.desc() (pixlstash/db_models/picture.py), so SQLite's
// native NULL rule applies - NULL is less than every real value, hence NULLs
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

// Verbatim copy of ImageGrid.vue's formatGridSmartScoreValue - what the card
// renders. A null smart score must show "" (no score), never "0.00".
function formatGridSmartScoreValue(img) {
  const value = getGridSmartScoreValue(img);
  return value === null ? "" : value.toFixed(2);
}

// Verbatim copy of ImageGrid.vue's shared smartScoreSortKey (the ONE null rule
// both the fresh-insert and reposition paths route through). Takes a raw value.
function smartScoreSortKey(smartScore) {
  return Number.isFinite(smartScore) ? smartScore : -Infinity;
}

// Verbatim copy of insertGridImagesById's insert loop (smart-score sort).
function insertByServerOrder(base, pic, descending) {
  const key = smartScoreSortKey(getGridSmartScoreValue(pic));
  let insertIndex = base.findIndex((img) => {
    const otherKey = smartScoreSortKey(getGridSmartScoreValue(img));
    return descending ? otherKey < key : otherKey > key;
  });
  if (insertIndex === -1) insertIndex = base.length;
  const next = base.slice();
  next.splice(insertIndex, 0, pic);
  return next;
}

// Verbatim copy of ImageGrid.vue's _spliceAndReinsert + repositionImageBySmartScore
// (the SCORE-CHANGED path): re-rank a card already in the grid after its smart
// score changes, deriving the ordering key from the null rule while storing the
// TRUE score on the card so display and ordering stay separate.
function repositionBySmartScore(base, imageId, smartScore, descending) {
  const items = base.slice();
  const currentIndex = items.findIndex((item) => item.id === imageId);
  if (currentIndex === -1) return items;

  const normalisedScore = Number.isFinite(smartScore) ? smartScore : null;
  const targetKey = smartScoreSortKey(normalisedScore);
  // Write both keys, mirroring the source: getGridSmartScoreValue reads
  // smartScore then falls back to smart_score, so a stale snake value must not
  // survive when the score becomes null.
  const target = {
    ...items[currentIndex],
    smartScore: normalisedScore,
    smart_score: normalisedScore,
  };

  items.splice(currentIndex, 1);
  let insertIndex = items.findIndex((item) => {
    const score = smartScoreSortKey(getGridSmartScoreValue(item));
    return descending ? score < targetKey : score > targetKey;
  });
  if (insertIndex === -1) insertIndex = items.length;
  items.splice(insertIndex, 0, target);
  return items;
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

describe("grid smart-score sort - null placement matches the server", () => {
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
    expect(smartScoreSortKey(getGridSmartScoreValue(nullCard))).toBe(-Infinity);
    expect(smartScoreSortKey(-100)).toBeGreaterThan(
      smartScoreSortKey(getGridSmartScoreValue(nullCard)),
    );
    // A genuine zero is a real value, never collapsed to the null sentinel.
    expect(smartScoreSortKey(0)).toBe(0);
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

// The SCORE-CHANGED / reposition path: a tag edit invalidates a card's smart
// score (interactive smart-score invalidation drives
// refreshSmartScoreForImage → repositionImageBySmartScore), so a card already in
// the grid is re-ranked to null. It must land where the server would put it AND
// keep displaying "no score", not a fabricated 0.
describe("grid smart-score reposition - null re-rank matches the server", () => {
  const withScores = [
    { id: 1, smart_score: 0.9 },
    { id: 2, smart_score: 0.1 },
    { id: 3, smart_score: -0.4 },
    { id: 4, smart_score: 0.0 },
  ];

  it("descending: a card rescored to null moves to LAST, like the server", () => {
    const descending = true;
    // id 2 (0.1) currently sits mid-pack; rescoring it to null must send it last.
    const base = serverOrder(withScores, descending);
    const result = repositionBySmartScore(base, 2, null, descending);
    const expected = serverOrder(
      withScores.map((c) => (c.id === 2 ? { ...c, smart_score: null } : c)),
      descending,
    );
    expect(ids(result)).toEqual(ids(expected));
    expect(ids(result)[ids(result).length - 1]).toBe(2);
  });

  it("ascending: a card rescored to null moves to FIRST, like the server", () => {
    const descending = false;
    const base = serverOrder(withScores, descending);
    const result = repositionBySmartScore(base, 2, null, descending);
    const expected = serverOrder(
      withScores.map((c) => (c.id === 2 ? { ...c, smart_score: null } : c)),
      descending,
    );
    expect(ids(result)).toEqual(ids(expected));
    expect(ids(result)[0]).toBe(2);
  });

  it("a card rescored to null still displays 'no score', not 0", () => {
    for (const descending of [true, false]) {
      const base = serverOrder(withScores, descending);
      const result = repositionBySmartScore(base, 2, null, descending);
      const moved = result.find((img) => img.id === 2);
      expect(getGridSmartScoreValue(moved)).toBeNull();
      expect(formatGridSmartScoreValue(moved)).toBe("");
    }
  });

  it("does not regress a negative rescore (0 is a real value, not a floor)", () => {
    const descending = true;
    // id 1 (0.9, currently first) rescored to -0.9 must sink below every other
    // real score but stay ABOVE any null card - proving 0 is not used as a floor.
    const withNull = [...withScores, { id: 5, smart_score: null }];
    const base = serverOrder(withNull, descending);
    const result = repositionBySmartScore(base, 1, -0.9, descending);
    const expected = serverOrder(
      withNull.map((c) => (c.id === 1 ? { ...c, smart_score: -0.9 } : c)),
      descending,
    );
    expect(ids(result)).toEqual(ids(expected));
    const moved = result.find((img) => img.id === 1);
    expect(getGridSmartScoreValue(moved)).toBe(-0.9);
    expect(formatGridSmartScoreValue(moved)).toBe("-0.90");
    // The null card is still dead last on descending, below the -0.9 card.
    expect(ids(result)[ids(result).length - 1]).toBe(5);
  });
});
