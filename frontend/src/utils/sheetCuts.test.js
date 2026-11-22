import { describe, expect, it } from "vitest";

import { MIN_SHARE, NUDGE, moveBox, nudgeBox, resizeBox } from "./sheetCuts";

const BOX = [0.25, 0.25, 0.75, 0.75];

describe("sheetCuts", () => {
  it("moves a box by its shares and holds it on the sheet", () => {
    expect(moveBox(BOX, 0.1, -0.1)).toEqual([0.35, 0.15, 0.85, 0.65]);
    expect(moveBox(BOX, 1, 1)).toEqual([0.5, 0.5, 1, 1]);
    expect(moveBox(BOX, -1, -1)).toEqual([0, 0, 0.5, 0.5]);
  });

  it("resizes only the edges named, never past the sheet or a sliver", () => {
    expect(resizeBox(BOX, ["r"], 0.1, 0)).toEqual([0.25, 0.25, 0.85, 0.75]);
    expect(resizeBox(BOX, ["l", "t"], -1, -1)).toEqual([0, 0, 0.75, 0.75]);
    expect(resizeBox(BOX, ["r", "b"], 0.5, 0.5)).toEqual([0.25, 0.25, 1, 1]);
    // A side dragged past its opposite edge stops a MIN_SHARE short of it.
    const [left] = resizeBox(BOX, ["l"], 0.9, 0);
    expect(BOX[2] - left).toBeCloseTo(MIN_SHARE);
    const [, top] = resizeBox(BOX, ["t"], 0, 0.9);
    expect(BOX[3] - top).toBeCloseTo(MIN_SHARE);
  });

  it("nudges the box with a plain arrow", () => {
    expect(nudgeBox(BOX, "ArrowRight")).toEqual(moveBox(BOX, NUDGE, 0));
    expect(nudgeBox(BOX, "ArrowUp")).toEqual(moveBox(BOX, 0, -NUDGE));
    expect(nudgeBox(BOX, "Enter")).toBeNull();
  });

  it("pushes the edge on the arrow's side outward with Shift, inward with Alt", () => {
    expect(nudgeBox(BOX, "ArrowLeft", { shift: true })[0]).toBeCloseTo(0.25 - NUDGE);
    expect(nudgeBox(BOX, "ArrowLeft", { alt: true })[0]).toBeCloseTo(0.25 + NUDGE);
    expect(nudgeBox(BOX, "ArrowDown", { shift: true })[3]).toBeCloseTo(0.75 + NUDGE);
    expect(nudgeBox(BOX, "ArrowDown", { alt: true })[3]).toBeCloseTo(0.75 - NUDGE);
    // The other edges stay where they were.
    expect(nudgeBox(BOX, "ArrowRight", { shift: true })).toEqual([0.25, 0.25, 0.75 + NUDGE, 0.75]);
  });
});
