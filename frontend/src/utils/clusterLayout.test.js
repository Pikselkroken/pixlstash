import { describe, expect, it } from "vitest";
import { STEP_LADDER, bandOf, layoutCluster } from "./clusterLayout.js";

const members = (n) => Array.from({ length: n }, (_, i) => ({ picture_id: i + 1 }));

function apart(a, b) {
  return (
    a.x + a.size <= b.x ||
    b.x + b.size <= a.x ||
    a.y + a.size <= b.y ||
    b.y + b.size <= a.y
  );
}

describe("layoutCluster", () => {
  it("never overlaps two faces", () => {
    for (const n of [1, 2, 9, 34, 60]) {
      const { items } = layoutCluster(members(n));
      expect(items).toHaveLength(n);
      for (const a of items) {
        for (const b of items) if (a !== b) expect(apart(a, b)).toBe(true);
      }
    }
  });

  it("puts the most typical face nearest the middle, at the biggest size", () => {
    const { items } = layoutCluster(members(34));
    const [top] = items;
    const cx = top.x + top.size / 2;
    const cy = top.y + top.size / 2;
    const dist = (p) => Math.hypot(p.x + p.size / 2 - cx, p.y + p.size / 2 - cy);
    // Every other face of the top band is closer to it than any of the last band.
    const lastBand = items.filter((p) => p.band === 3);
    const nearest = Math.min(...lastBand.map(dist));
    expect(items.filter((p) => p.band === 0 && p !== top).every((p) => dist(p) < nearest)).toBe(true);
    expect(top.size).toBe(96);
  });

  it("takes sizes only from the ladder, in unequal bands", () => {
    const { items } = layoutCluster(members(34));
    expect(new Set(items.map((p) => p.size))).toEqual(new Set(STEP_LADDER[0]));
    // Ranks 0-3 of 34 are under 10%: four faces at the top step.
    expect(items.filter((p) => p.size === 96)).toHaveLength(4);
    expect(bandOf(0, 2)).toBe(0);
    expect(bandOf(1, 2)).toBe(2);
  });

  it("fits its box: first by cutting the spacing, then with smaller faces", () => {
    const big = layoutCluster(members(34));
    // Just under the spaced size: the gap goes, the faces keep their size.
    const tight = layoutCluster(members(34), STEP_LADDER, {
      width: big.width - 1,
      height: big.height + 200,
    });
    expect(tight.fits).toBe(true);
    expect(tight.width).toBeLessThanOrEqual(big.width - 1);
    expect(tight.items[0].size).toBe(96);
    // Much smaller: a smaller set of sizes, still inside the box.
    const small = layoutCluster(members(34), STEP_LADDER, { width: 320, height: 320 });
    expect(small.fits).toBe(true);
    expect(small.items[0].size).toBeLessThan(96);
    expect(small.width).toBeLessThanOrEqual(320);
    expect(small.height).toBeLessThanOrEqual(320);
    // No room at all: it says so, for the caller to scale.
    expect(layoutCluster(members(60), STEP_LADDER, { width: 50, height: 50 }).fits).toBe(false);
  });
});
