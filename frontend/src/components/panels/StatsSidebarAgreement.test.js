// Agreement matrix in the stats sidebar - the 5x4 cross-tab of the user's star
// rating against the smart score.
//
// StatsSidebar.vue is ~2.7k lines and its data comes from a live /pictures/stats
// call, so - following the ImageGridLockBadge.test.js / ImageOverlayContextMenu
// precedent - these tests reproduce the widget's pure contracts verbatim from
// the component rather than mounting it:
//
//   1. the sqrt shade ramp (what makes a small cell visible next to a huge one);
//   2. the on-fill ink switch for the count label;
//   3. the compound cell filter and its toggle-off;
//   4. the roving-tabindex keyboard model;
//   5. the tau / coverage copy, including the suppressed-coefficient case.

import { describe, it, expect } from "vitest";

const AGREEMENT_STARS = [1, 2, 3, 4, 5];
const AGREEMENT_BUCKETS = ["1-2", "2-3", "3-4", "4-5"];
const AGREEMENT_ON_FILL_SHADE = 0.55;

function makeCounts(pairs) {
  const map = new Map();
  for (const [star, bucket, count] of pairs) map.set(`${star}|${bucket}`, count);
  return map;
}

const countOf = (counts, star, bucket) => counts.get(`${star}|${bucket}`) || 0;
const maxOf = (counts) => Math.max(0, ...counts.values());

// (1) Verbatim copy of agreementShade().
function agreementShade(counts, star, bucket) {
  const count = countOf(counts, star, bucket);
  const max = maxOf(counts);
  if (!count || !max) return 0;
  const ratio = Math.sqrt(count / max);
  return 0.12 + ratio * 0.73;
}

// (3) Verbatim copy of isAgreementCellActive() + onAgreementCellClick(), against
// a fake filter state standing in for the three props/emits.
function isActive(filters, star, bucket) {
  return (
    filters.minScore === star &&
    filters.maxScore === star &&
    filters.bucket === bucket
  );
}

function clickCell(filters, counts, star, bucket) {
  if (countOf(counts, star, bucket) <= 0) return filters;
  if (isActive(filters, star, bucket)) {
    return { minScore: null, maxScore: null, bucket: null };
  }
  return { minScore: star, maxScore: star, bucket };
}

// (4) Verbatim copy of the arrow-key branch of onAgreementKeydown().
function moveFocus({ row, col }, key) {
  const lastRow = AGREEMENT_STARS.length - 1;
  const lastCol = AGREEMENT_BUCKETS.length - 1;
  if (key === "ArrowRight") return { row, col: Math.min(lastCol, col + 1) };
  if (key === "ArrowLeft") return { row, col: Math.max(0, col - 1) };
  if (key === "ArrowDown") return { row: Math.min(lastRow, row + 1), col };
  if (key === "ArrowUp") return { row: Math.max(0, row - 1), col };
  if (key === "Home") return { row, col: 0 };
  if (key === "End") return { row, col: lastCol };
  return null;
}

// (5) Verbatim copies of formatCoefficient / agreementCoverage / the tone rule.
function formatCoefficient(value) {
  if (value == null || Number.isNaN(Number(value))) return null;
  const rounded = Number(value).toFixed(2);
  return Number(rounded) > 0 ? `+${rounded}` : rounded;
}

// (6) Verbatim copies of agreementDisagreement / agreementTone.
const AGREEMENT_BUCKET_RANGES = {
  "1-2": [1, 2],
  "2-3": [2, 3],
  "3-4": [3, 4],
  "4-5": [4, 5],
};
const AGREEMENT_MATCH_RADIUS = 0.5;

function disagreement(star, bucket) {
  const range = AGREEMENT_BUCKET_RANGES[bucket];
  if (!range) return 0;
  const [low, high] = range;
  return Math.max(low - star, star - high, 0);
}

function tone(star, bucket) {
  const d = disagreement(star, bucket);
  if (d <= AGREEMENT_MATCH_RADIUS) return "good";
  if (d <= 1 + AGREEMENT_MATCH_RADIUS) return "mixed";
  return "bad";
}

function coverage({ rated, total }) {
  if (!total) return "No pictures in view";
  const pct = Math.round((rated / total) * 100);
  return `${rated.toLocaleString()} of ${total.toLocaleString()} rated (${pct}%)`;
}

describe("agreement shade ramp", () => {
  it("gives an empty cell no fill at all", () => {
    const counts = makeCounts([
      [1, "1-2", 0],
      [5, "4-5", 40],
    ]);
    expect(agreementShade(counts, 1, "1-2")).toBe(0);
  });

  it("gives the largest cell the darkest step", () => {
    const counts = makeCounts([
      [1, "1-2", 10],
      [5, "4-5", 40],
    ]);
    expect(agreementShade(counts, 5, "4-5")).toBeCloseTo(0.85, 5);
  });

  it("keeps a small cell visible beside a dominant one (this is why it is sqrt)", () => {
    // 1/100th of the max would be a 0.0073 wash on a linear ramp; sqrt lifts it
    // to a step you can actually see.
    const counts = makeCounts([
      [1, "1-2", 1],
      [5, "4-5", 100],
    ]);
    const small = agreementShade(counts, 1, "1-2");
    expect(small).toBeGreaterThan(0.15);
    const linear = 0.12 + (1 / 100) * 0.73;
    expect(small).toBeGreaterThan(linear);
  });

  it("is monotonic in the count", () => {
    const counts = makeCounts([
      [1, "1-2", 5],
      [2, "2-3", 20],
      [3, "3-4", 50],
    ]);
    expect(agreementShade(counts, 1, "1-2")).toBeLessThan(
      agreementShade(counts, 2, "2-3"),
    );
    expect(agreementShade(counts, 2, "2-3")).toBeLessThan(
      agreementShade(counts, 3, "3-4"),
    );
  });
});

describe("count label ink", () => {
  it("switches to on-primary ink once the fill is dark", () => {
    const counts = makeCounts([[5, "4-5", 40]]);
    expect(agreementShade(counts, 5, "4-5")).toBeGreaterThanOrEqual(
      AGREEMENT_ON_FILL_SHADE,
    );
  });

  it("keeps on-surface ink on a pale cell", () => {
    const counts = makeCounts([
      [1, "1-2", 1],
      [5, "4-5", 400],
    ]);
    expect(agreementShade(counts, 1, "1-2")).toBeLessThan(
      AGREEMENT_ON_FILL_SHADE,
    );
  });
});

describe("cell click filter", () => {
  const counts = makeCounts([
    [5, "1-2", 7],
    [5, "4-5", 40],
    [1, "4-5", 0],
  ]);
  const cleared = { minScore: null, maxScore: null, bucket: null };

  it("sets the row and the column together", () => {
    expect(clickCell(cleared, counts, 5, "1-2")).toEqual({
      minScore: 5,
      maxScore: 5,
      bucket: "1-2",
    });
  });

  it("clicking the active cell clears both halves", () => {
    const active = { minScore: 5, maxScore: 5, bucket: "1-2" };
    expect(clickCell(active, counts, 5, "1-2")).toEqual(cleared);
  });

  it("moving to another cell replaces the filter rather than merging it", () => {
    const active = { minScore: 5, maxScore: 5, bucket: "1-2" };
    expect(clickCell(active, counts, 5, "4-5")).toEqual({
      minScore: 5,
      maxScore: 5,
      bucket: "4-5",
    });
  });

  it("an empty cell is inert - it would filter the grid to nothing", () => {
    expect(clickCell(cleared, counts, 1, "4-5")).toEqual(cleared);
  });

  it("only the exact row+column combination reads as selected", () => {
    const active = { minScore: 5, maxScore: 5, bucket: "1-2" };
    expect(isActive(active, 5, "1-2")).toBe(true);
    expect(isActive(active, 5, "4-5")).toBe(false);
    expect(isActive(active, 4, "1-2")).toBe(false);
    // A plain score filter set from the Score histogram must not light up a cell.
    expect(isActive({ minScore: 5, maxScore: 5, bucket: null }, 5, "1-2")).toBe(
      false,
    );
  });
});

describe("keyboard model", () => {
  it("arrows move within the grid", () => {
    expect(moveFocus({ row: 2, col: 1 }, "ArrowRight")).toEqual({
      row: 2,
      col: 2,
    });
    expect(moveFocus({ row: 2, col: 1 }, "ArrowUp")).toEqual({ row: 1, col: 1 });
  });

  it("clamps at every edge instead of wrapping", () => {
    expect(moveFocus({ row: 0, col: 0 }, "ArrowUp")).toEqual({ row: 0, col: 0 });
    expect(moveFocus({ row: 0, col: 0 }, "ArrowLeft")).toEqual({
      row: 0,
      col: 0,
    });
    expect(moveFocus({ row: 4, col: 3 }, "ArrowDown")).toEqual({
      row: 4,
      col: 3,
    });
    expect(moveFocus({ row: 4, col: 3 }, "ArrowRight")).toEqual({
      row: 4,
      col: 3,
    });
  });

  it("Home and End jump along the row", () => {
    expect(moveFocus({ row: 3, col: 2 }, "Home")).toEqual({ row: 3, col: 0 });
    expect(moveFocus({ row: 3, col: 2 }, "End")).toEqual({ row: 3, col: 3 });
  });

  it("ignores unrelated keys", () => {
    expect(moveFocus({ row: 1, col: 1 }, "PageDown")).toBe(null);
    expect(moveFocus({ row: 1, col: 1 }, "a")).toBe(null);
  });
});

describe("traffic-light tone", () => {
  // A star rating is a rounded smart score: rating 4 stands for 3.5 to 4.5, so it
  // matches BOTH the 3-4 and the 4-5 bucket. Measuring the gap in grid steps
  // instead of score points made rating 4 a near-miss against 4-5 - the bug this
  // block now pins down.
  it("treats a rating as a match for every bucket within half a point", () => {
    expect(tone(4, "3-4")).toBe("good");
    expect(tone(4, "4-5")).toBe("good");
    expect(tone(2, "1-2")).toBe("good");
    expect(tone(2, "2-3")).toBe("good");
    expect(tone(3, "2-3")).toBe("good");
    expect(tone(3, "3-4")).toBe("good");
  });

  it("matches the end ratings to the single bucket their range reaches", () => {
    // Rating 1 covers 0.5-1.5 and rating 5 covers 4.5-5.5, so each overlaps one
    // bucket only. That asymmetry is real, not a mapping artefact.
    expect(tone(1, "1-2")).toBe("good");
    expect(tone(1, "2-3")).toBe("mixed");
    expect(tone(5, "4-5")).toBe("good");
    expect(tone(5, "3-4")).toBe("mixed");
  });

  it("paints the far corners red", () => {
    expect(tone(1, "4-5")).toBe("bad");
    expect(tone(5, "1-2")).toBe("bad");
    expect(tone(1, "3-4")).toBe("bad");
    expect(tone(5, "2-3")).toBe("bad");
  });

  it("measures the gap in smart-score points from the nearest edge of the bucket", () => {
    expect(disagreement(4, "4-5")).toBe(0); // inside the bucket
    expect(disagreement(4, "3-4")).toBe(0); // on its upper edge
    expect(disagreement(4, "2-3")).toBe(1); // one point below
    expect(disagreement(4, "1-2")).toBe(2);
    expect(disagreement(1, "4-5")).toBe(3); // the worst possible disagreement
  });

  it("is symmetric: disagreeing in either direction reads the same", () => {
    expect(tone(1, "4-5")).toBe(tone(5, "1-2"));
    expect(tone(2, "4-5")).toBe(tone(4, "1-2"));
    expect(disagreement(2, "4-5")).toBe(disagreement(4, "1-2"));
  });

  it("gets worse monotonically as a row moves away from its matching column", () => {
    expect(disagreement(1, "1-2")).toBeLessThan(disagreement(1, "2-3"));
    expect(disagreement(1, "2-3")).toBeLessThan(disagreement(1, "3-4"));
    expect(disagreement(1, "3-4")).toBeLessThan(disagreement(1, "4-5"));
  });

  it("gives every middle rating a two-bucket green band and the ends one", () => {
    const greenPerRating = AGREEMENT_STARS.map(
      (star) =>
        AGREEMENT_BUCKETS.filter((bucket) => tone(star, bucket) === "good")
          .length,
    );
    expect(greenPerRating).toEqual([1, 2, 2, 2, 1]);
  });

  it("covers every cell with a tone", () => {
    for (const star of AGREEMENT_STARS) {
      for (const bucket of AGREEMENT_BUCKETS) {
        expect(["good", "mixed", "bad"]).toContain(tone(star, bucket));
      }
    }
  });
});

describe("summary copy", () => {
  it("signs a positive coefficient explicitly", () => {
    expect(formatCoefficient(0.42)).toBe("+0.42");
  });

  it("shows a negative coefficient (the machine disagrees with you)", () => {
    expect(formatCoefficient(-0.31)).toBe("-0.31");
  });

  it("shows a flat coefficient without a misleading plus sign", () => {
    expect(formatCoefficient(0)).toBe("0.00");
  });

  it("returns null for a suppressed coefficient so the row is dropped, not blank", () => {
    expect(formatCoefficient(null)).toBe(null);
    expect(formatCoefficient(undefined)).toBe(null);
  });

  it("always shows two decimals, so the two rows line up", () => {
    expect(formatCoefficient(0.5)).toBe("+0.50");
    expect(formatCoefficient(-1)).toBe("-1.00");
  });

  it("reports coverage against the whole view, not just the rated part", () => {
    expect(coverage({ rated: 312, total: 12431 })).toBe(
      "312 of 12,431 rated (3%)",
    );
  });

  it("handles the zero-rated empty state", () => {
    expect(coverage({ rated: 0, total: 12431 })).toBe(
      "0 of 12,431 rated (0%)",
    );
  });

  it("handles an empty view without dividing by zero", () => {
    expect(coverage({ rated: 0, total: 0 })).toBe("No pictures in view");
  });
});

describe("grid shape", () => {
  it("is the cross-product of the two histograms above it", () => {
    expect(AGREEMENT_STARS).toEqual([1, 2, 3, 4, 5]);
    expect(AGREEMENT_BUCKETS).toEqual(["1-2", "2-3", "3-4", "4-5"]);
    expect(AGREEMENT_STARS.length * AGREEMENT_BUCKETS.length).toBe(20);
  });
});
