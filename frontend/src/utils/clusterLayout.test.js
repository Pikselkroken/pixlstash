import { describe, expect, it } from "vitest";
import { STEP_LADDER, bandOf, layoutCluster, tileOf } from "./clusterLayout.js";

const members = (n) => Array.from({ length: n }, (_, i) => ({ picture_id: i + 1 }));
// Portrait, landscape, square and a panorama past the limit, in turn.
const ASPECTS = [2 / 3, 16 / 9, 1, 4];
const shaped = (n) => members(n).map((m, i) => ({ ...m, aspect: ASPECTS[i % 4] }));

function apart(a, b) {
  return (
    a.x + a.w <= b.x ||
    b.x + b.w <= a.x ||
    a.y + a.h <= b.y ||
    b.y + b.h <= a.y
  );
}

describe("layoutCluster", () => {
  it("never overlaps two faces", () => {
    for (const n of [1, 2, 9, 34, 60]) {
      for (const list of [members(n), shaped(n)]) {
        const { items } = layoutCluster(list);
        expect(items).toHaveLength(n);
        for (const a of items) {
          for (const b of items) if (a !== b) expect(apart(a, b)).toBe(true);
        }
      }
    }
  });

  it("keeps each picture's aspect ratio, its long side the band's size", () => {
    const { items } = layoutCluster(shaped(34));
    const [portrait, landscape, square, panorama] = items;
    expect([portrait.w, portrait.h]).toEqual([64, 96]);
    expect([landscape.w, landscape.h]).toEqual([96, 54]);
    expect([square.w, square.h]).toEqual([96, 96]);
    // A panorama stops at 2:1 rather than turning into a sliver.
    expect([panorama.w, panorama.h]).toEqual([96, 48]);
    expect(tileOf(32, 0.25)).toEqual({ w: 16, h: 32 });
    expect(tileOf(32)).toEqual({ w: 32, h: 32 });
  });

  it("puts the most typical face nearest the middle, at the biggest size", () => {
    const { items } = layoutCluster(members(34));
    const [top] = items;
    const cx = top.x + top.w / 2;
    const cy = top.y + top.h / 2;
    const dist = (p) => Math.hypot(p.x + p.w / 2 - cx, p.y + p.h / 2 - cy);
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
