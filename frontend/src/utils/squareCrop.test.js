import { describe, it, expect } from "vitest";
import {
  squareCropParams,
  squareCropImgStyle,
  squareCropBboxRect,
  coverBboxRect,
} from "./squareCrop.js";

describe("squareCropParams", () => {
  it("returns normalised params for a landscape crop", () => {
    // 800x600 bitmap, face-weighted square (side 600) offset right.
    const p = squareCropParams({
      thumbnail_width: 800,
      thumbnail_height: 600,
      square_crop_x: 120,
      square_crop_y: 0,
      square_crop_side: 600,
    });
    expect(p).toEqual({ tw: 800, th: 600, cx: 120, cy: 0, side: 600 });
  });

  it("derives side = min(w, h) when square_crop_side is absent", () => {
    const p = squareCropParams({
      thumbnail_width: 500,
      thumbnail_height: 900,
      square_crop_x: 0,
      square_crop_y: 200,
    });
    expect(p.side).toBe(500);
  });

  it("returns null when the crop origin is missing (unprocessed picture)", () => {
    expect(
      squareCropParams({
        thumbnail_width: 800,
        thumbnail_height: 600,
        square_crop_x: null,
        square_crop_y: null,
      }),
    ).toBeNull();
  });

  it("returns null when bitmap dims are missing", () => {
    expect(
      squareCropParams({ square_crop_x: 0, square_crop_y: 0 }),
    ).toBeNull();
    expect(squareCropParams(null)).toBeNull();
  });
});

describe("squareCropImgStyle", () => {
  it("scales and translates a landscape bitmap so the crop fills the cell", () => {
    // 800x600, side 600, crop at x=120. scale = S/side; img width = tw/side.
    const style = squareCropImgStyle({
      thumbnail_width: 800,
      thumbnail_height: 600,
      square_crop_x: 120,
      square_crop_y: 0,
      square_crop_side: 600,
    });
    // width = 800/600 = 133.33% ; height = 600/600 = 100%
    expect(style.width).toBe(`${(800 / 600) * 100}%`);
    expect(style.height).toBe("100%");
    // left = -120/600 = -20% ; top = 0%
    expect(style.left).toBe(`${(-120 / 600) * 100}%`);
    expect(style.top).toBe("0%");
    expect(style.objectFit).toBe("fill");
    expect(style.aspectRatio).toBe("auto");
  });

  it("scales and translates a portrait bitmap (top-anchored crop)", () => {
    // 500x900, side 500, crop at y=200.
    const style = squareCropImgStyle({
      thumbnail_width: 500,
      thumbnail_height: 900,
      square_crop_x: 0,
      square_crop_y: 200,
      square_crop_side: 500,
    });
    // width = 500/500 = 100% ; height = 900/500 = 180%
    expect(style.width).toBe("100%");
    expect(style.height).toBe(`${(900 / 500) * 100}%`);
    // top = -200/500 = -40%
    expect(style.top).toBe(`${(-200 / 500) * 100}%`);
    expect(style.left).toBe("0%");
  });

  it("returns null (fallback to CSS cover) when crop fields are null", () => {
    expect(
      squareCropImgStyle({
        thumbnail_width: 800,
        thumbnail_height: 600,
        square_crop_x: null,
        square_crop_y: null,
      }),
    ).toBeNull();
  });
});

describe("squareCropBboxRect", () => {
  it("subtracts the crop offset and scales a face box into cell pixels", () => {
    // side 600 crop at x=120, rendered into a 300px cell → scale 0.5.
    const params = { cx: 120, cy: 0, side: 600 };
    // Face bbox at bitmap (200,100)-(320,260).
    const rect = squareCropBboxRect([200, 100, 320, 260], params, 300);
    // left = (200-120)*0.5 = 40 ; top = (100-0)*0.5 = 50
    expect(rect.left).toBe(40);
    expect(rect.top).toBe(50);
    expect(rect.width).toBe((320 - 200) * 0.5);
    expect(rect.height).toBe((260 - 100) * 0.5);
  });

  it("produces negative/oversized coords for boxes outside the crop (clipped by the cell)", () => {
    const params = { cx: 120, cy: 0, side: 600 };
    // Box entirely left of the crop window.
    const rect = squareCropBboxRect([0, 0, 60, 60], params, 300);
    expect(rect.left).toBeLessThan(0);
  });
});

describe("coverBboxRect", () => {
  it("maps a bbox with object-fit:cover, top-anchored, horizontally centred", () => {
    // 800x600 bitmap into a 300x300 cell. cover scale = max(300/800, 300/600)=0.5.
    const rect = coverBboxRect([100, 50, 300, 250], 800, 600, 300, 300);
    const scale = Math.max(300 / 800, 300 / 600); // 0.5
    const displayWidth = 800 * scale; // 400
    const offsetX = (300 - displayWidth) / 2; // -50
    expect(rect.left).toBe(offsetX + 100 * scale);
    expect(rect.top).toBe(50 * scale); // offsetY = 0
    expect(rect.width).toBe((300 - 100) * scale);
    expect(rect.height).toBe((250 - 50) * scale);
  });
});
