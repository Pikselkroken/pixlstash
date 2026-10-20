import { describe, expect, it } from "vitest";
import { cropFramings, nearestBucket } from "./cropFramings";

const near = (box, expected) =>
  box.forEach((value, index) => expect(value).toBeCloseTo(expected[index], 4));

describe("cropFramings", () => {
  it("offers a square on the face only when there is one", () => {
    expect(cropFramings(1000, 1000).map((f) => f.id)).toEqual([
      "portrait",
      "bucket",
    ]);
    const [square] = cropFramings(1000, 2000, [400, 200, 600, 400]);
    expect(square.id).toBe("face");
    // 2.5 face heights (500 px), centred on the face at (500, 300).
    near(square.box, [0.25, 0.025, 0.5, 0.25]);
  });

  it("keeps every box inside the picture", () => {
    // A face against the top-left corner pushes the boxes to the edge.
    for (const { box } of cropFramings(1200, 800, [0, 0, 100, 100])) {
      const [left, top, width, height] = box;
      expect(left).toBeGreaterThanOrEqual(0);
      expect(top).toBeGreaterThanOrEqual(0);
      expect(left + width).toBeLessThanOrEqual(1 + 1e-9);
      expect(top + height).toBeLessThanOrEqual(1 + 1e-9);
    }
  });

  it("makes 3:4 the largest portrait box, centred without a face", () => {
    const portrait = cropFramings(1600, 1200).find((f) => f.id === "portrait");
    // 900 x 1200 of a 1600 x 1200 picture, centred across.
    near(portrait.box, [350 / 1600, 0, 900 / 1600, 1]);
  });

  it("picks the bucket nearest the picture's shape", () => {
    expect(nearestBucket(1000, 1000)).toEqual([1024, 1024]);
    expect(nearestBucket(1920, 1080)).toEqual([1344, 768]);
    expect(nearestBucket(800, 1200)).toEqual([832, 1216]);
  });
});
