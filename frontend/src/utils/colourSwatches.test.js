import { describe, expect, it } from "vitest";

import { COLOUR_SWATCHES, nearestSwatch } from "./colourSwatches";

describe("nearestSwatch", () => {
  it("puts each swatch's own centre in that swatch", () => {
    for (const swatch of COLOUR_SWATCHES) {
      expect(nearestSwatch(swatch.fill).id).toBe(swatch.id);
    }
  });

  it("puts a picked colour in the swatch it reads as", () => {
    expect(nearestSwatch("#ff0000").id).toBe("red");
    expect(nearestSwatch("#1e90ff").id).toBe("blue");
    expect(nearestSwatch("#ffffff").id).toBe("white");
    expect(nearestSwatch("#000000").id).toBe("black");
    expect(nearestSwatch("#7f7f7f").id).toBe("grey");
  });
});
