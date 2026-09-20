import { describe, it, expect } from "vitest";
import {
  squareCropParams,
  squareCropImgStyle,
  squareCropBboxRect,
  coverBboxRect,
  cropRectForRatio,
  cropImgStyle,
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
    expect(style.objectFit).toBe("cover");
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

// A workflow generation as the app actually stores one: 832×1216 becomes a
// 384×561 bitmap, and the face-weighted square sits BELOW the top edge, which
// is the case #1465 is about — a blind top anchor cuts through the face that
// the stored rectangle was computed to keep. The square spans y 120…504, so
// its centre of interest is (192, 312).
const PORTRAIT = {
  thumbnail_width: 384,
  thumbnail_height: 561,
  square_crop_x: 0,
  square_crop_y: 120,
  square_crop_side: 384,
};

describe("cropRectForRatio", () => {
  it("is the stored rectangle itself at ratio 1", () => {
    expect(cropRectForRatio(squareCropParams(PORTRAIT), 1)).toEqual({
      x: 0,
      y: 120,
      w: 384,
      h: 384,
    });
  });

  it("centres the card's 6:5 cover on the face instead of the top edge", () => {
    // 6:5 is wider than the bitmap allows around a 384 square, so the crop is
    // the full width and 384/1.2 = 320 tall — the same SHAPE the top anchor
    // takes, moved from y=0 to the one that keeps the face: 312 - 160.
    expect(cropRectForRatio(squareCropParams(PORTRAIT), 6 / 5)).toEqual({
      x: 0,
      y: 152,
      w: 384,
      h: 320,
    });
  });

  it("does the same for the mosaic's 4:5 cells, which keep more height", () => {
    // 384 / 0.8 = 480 tall, centred at 312 → 72. A taller cell needs to move
    // less, which is why the mosaic loses fewer faces than the single cover.
    expect(cropRectForRatio(squareCropParams(PORTRAIT), 4 / 5)).toEqual({
      x: 0,
      y: 72,
      w: 384,
      h: 480,
    });
  });

  it("clamps to the bitmap rather than reading past its edge", () => {
    // A 3:5 cell wants 640px of height from a 561px bitmap, so it takes all of
    // it and trims the sides instead: 561 × 0.6 = 336.6 wide, and y can only
    // be 0. The face's own x still decides WHICH sides come off, which
    // `object-position: top center` could not do.
    const rect = cropRectForRatio(squareCropParams(PORTRAIT), 3 / 5);
    expect(rect.y).toBe(0);
    expect(rect.h).toBe(561);
    expect(rect.w).toBeCloseTo(336.6, 6);
    expect(rect.x).toBeCloseTo(23.7, 6);
  });

  it("keeps a crop whose face sits against an edge inside the bitmap", () => {
    // An 800×600 bitmap whose square is hard against the right edge: centred
    // on it, a 720-wide 6:5 crop would start at 140 and end at 860.
    const rect = cropRectForRatio(
      squareCropParams({
        thumbnail_width: 800,
        thumbnail_height: 600,
        square_crop_x: 200,
        square_crop_y: 0,
        square_crop_side: 600,
      }),
      6 / 5,
    );
    expect(rect).toEqual({ x: 80, y: 0, w: 720, h: 600 });
    expect(rect.x + rect.w).toBe(800);
  });

  // The LOWER clamp, which is the one the library's commonest picture needs.
  // `FaceUtils.square_crop_rect` top-anchors every faceless portrait at y=0,
  // so a cell TALLER than the stored square asks to start above the bitmap:
  // the 4:5 cell wants 480 rows centred on 192, i.e. y = -48. Without
  // `Math.max(…, 0)` that negative survives, `cropImgStyle` emits a POSITIVE
  // `top`, and every mosaic cell paints a band of empty background across the
  // head of the picture.
  it("never starts above the bitmap when the square is at the top edge", () => {
    const params = squareCropParams({ ...PORTRAIT, square_crop_y: 0 });

    expect(cropRectForRatio(params, 4 / 5)).toEqual({
      x: 0,
      y: 0,
      w: 384,
      h: 480,
    });
    // Its neighbour needs no clamping and must NOT be pinned to 0: the 6:5
    // cell is shorter than the square, so it sits inside it at 192 - 160.
    expect(cropRectForRatio(params, 6 / 5).y).toBe(32);
  });

  it("never starts left of the bitmap either", () => {
    // The same guard on the other axis. A 800×400 bitmap's square is 400 wide
    // and hard against the LEFT edge, so its centre is 200; a 6:5 cell wants
    // 480 of width around it, i.e. x = -40.
    const rect = cropRectForRatio(
      squareCropParams({
        thumbnail_width: 800,
        thumbnail_height: 400,
        square_crop_x: 0,
        square_crop_y: 0,
        square_crop_side: 400,
      }),
      6 / 5,
    );
    expect(rect).toEqual({ x: 0, y: 0, w: 480, h: 400 });
  });

  it("refuses a ratio that is not a positive number", () => {
    const params = squareCropParams(PORTRAIT);
    expect(cropRectForRatio(params, 0)).toBeNull();
    expect(cropRectForRatio(params, NaN)).toBeNull();
  });
});

describe("cropImgStyle", () => {
  it("scales and translates the bitmap so the 6:5 crop fills the cell", () => {
    // The rect is 384×320 at (0, 152): the img is the bitmap's own size over
    // the crop's, offset by -crop, and the two denominators differ because
    // the cell is not square.
    const style = cropImgStyle(PORTRAIT, 6 / 5);
    expect(style.width).toBe("100%");
    expect(style.height).toBe(`${(561 / 320) * 100}%`);
    expect(style.left).toBe("0%");
    expect(style.top).toBe(`${(-152 / 320) * 100}%`);
    expect(style.objectFit).toBe("cover");
    // Overriding the cells' `object-position: top center`, which is the right
    // anchor for a crop nothing aimed and the wrong one for this: the residual
    // from a drifted cell ratio would come off the bottom, against the face.
    expect(style.objectPosition).toBe("center");
  });

  // `left` and `top` do NOT share a denominator: a percentage `left` resolves
  // against the cell's width and a percentage `top` against its height, so the
  // horizontal offset is over `w` and the vertical over `h`. Every other case
  // here has a crop at x=0, where "%" of anything is 0 and the two are
  // indistinguishable - so this is the only assertion that can tell them apart.
  it("divides the horizontal offset by the crop's WIDTH, not its height", () => {
    // A landscape bitmap in a tall 3:5 cell: 384 tall × 230.4 wide, and the
    // square's centre at x=280 puts the crop at x = 280 - 115.2 = 164.8.
    const style = cropImgStyle(
      {
        thumbnail_width: 561,
        thumbnail_height: 384,
        square_crop_x: 88,
        square_crop_y: 0,
        square_crop_side: 384,
      },
      3 / 5,
    );
    expect(style.left).toBe(`${(-164.8 / 230.4) * 100}%`);
    expect(style.top).toBe("0%");
    // Over the height it would read -42.9%, which is the mutation this catches.
    expect(style.left).not.toBe(`${(-164.8 / 384) * 100}%`);
    expect(style.width).toBe(`${(561 / 230.4) * 100}%`);
    expect(style.height).toBe("100%");
  });

  it("falls back to CSS cover when the rectangle has not been computed", () => {
    // The requirement, not a nicety: those covers must look exactly as they
    // did before the crop existed.
    expect(
      cropImgStyle(
        { ...PORTRAIT, square_crop_x: null, square_crop_y: null },
        6 / 5,
      ),
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
