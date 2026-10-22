import { describe, expect, it } from "vitest";
import {
  COMPARE_PICK_REASON,
  COMPARE_VIDEO_REASON,
  compareBlockReason,
} from "./compare";

const picture = (id, format = "png") => ({ id, format });

describe("compareBlockReason", () => {
  it("runs on exactly two pictures", () => {
    expect(compareBlockReason([picture(1), picture(2)])).toBeNull();
  });

  it("asks for two otherwise", () => {
    expect(compareBlockReason([])).toBe(COMPARE_PICK_REASON);
    expect(compareBlockReason([picture(1)])).toBe(COMPARE_PICK_REASON);
    expect(compareBlockReason([picture(1), picture(2), picture(3)])).toBe(
      COMPARE_PICK_REASON,
    );
  });

  it("refuses a video", () => {
    expect(compareBlockReason([picture(1), picture(2, "mp4")])).toBe(
      COMPARE_VIDEO_REASON,
    );
  });
});
