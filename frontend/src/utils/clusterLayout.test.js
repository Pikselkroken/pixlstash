import { describe, expect, it } from "vitest";
import { bandOf, layoutCluster } from "./clusterLayout.js";

const STEPS = [96, 64, 48, 32];
const members = (n) => Array.from({ length: n }, (_, i) => ({ picture_id: i + 1 }));

describe("layoutCluster", () => {
  it("never overlaps two faces", () => {
    for (const n of [1, 2, 9, 34, 60]) {
      const { items } = layoutCluster(members(n), STEPS, 4);
      expect(items).toHaveLength(n);
      for (const a of items) {
        for (const b of items) {
          if (a === b) continue;
          const apart =
            a.x + a.size <= b.x ||
            b.x + b.size <= a.x ||
            a.y + a.size <= b.y ||
            b.y + b.size <= a.y;
          expect(apart).toBe(true);
        }
      }
    }
  });

  it("puts the most typical face nearest the centre of the box", () => {
    const { items, width, height } = layoutCluster(members(34), STEPS);
    const dist = (p) =>
      Math.hypot(p.x + p.size / 2 - width / 2, p.y + p.size / 2 - height / 2);
    const first = dist(items[0]);
    const nearer = items.slice(1).filter((p) => dist(p) < first);
    // The box is the faces' extent, so its centre drifts a little from the
    // spiral's; the top face is still among the few nearest it.
    expect(nearer.length).toBeLessThanOrEqual(3);
    expect(items[0].size).toBe(96);
  });

  it("takes sizes only from the given steps, in unequal bands", () => {
    const { items } = layoutCluster(members(34), STEPS);
    expect(new Set(items.map((p) => p.size))).toEqual(new Set(STEPS));
    // Ranks 0-3 of 34 are under 10%: four faces at the top step.
    expect(items.filter((p) => p.size === 96)).toHaveLength(4);
    expect(bandOf(0, 2)).toBe(0);
    expect(bandOf(1, 2)).toBe(2);
  });
});
