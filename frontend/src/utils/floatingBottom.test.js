import { describe, it, expect } from "vitest";
import {
  FLOATING_BOTTOM_GAP_PX,
  SELBAR_FALLBACK_H_PX,
  computeFloatingBottomInset,
  toPx,
} from "./floatingBottom.js";

const pill = (height, visible = true) => ({ height, visible });
const breadcrumb = (height, visible = true) => ({
  height,
  visible,
  narrowOnly: true,
});

describe("computeFloatingBottomInset - nothing parked", () => {
  it("is 0 when there are no anchors", () => {
    expect(computeFloatingBottomInset()).toBe(0);
    expect(computeFloatingBottomInset({ anchors: [] })).toBe(0);
  });

  // 0, NOT the gap: with the bottom edge clear the stack must rest at exactly
  // --space-5, per the spec's "pill hidden → 16px".
  it("is 0 - not the gap - when every anchor is hidden", () => {
    expect(computeFloatingBottomInset({ anchors: [pill(54, false)] })).toBe(0);
  });

  it("ignores a zero or negative measurement", () => {
    expect(computeFloatingBottomInset({ anchors: [pill(0)] })).toBe(0);
    expect(computeFloatingBottomInset({ anchors: [pill(-10)] })).toBe(0);
  });

  it("ignores a malformed measurement rather than producing NaN", () => {
    expect(computeFloatingBottomInset({ anchors: [pill(undefined)] })).toBe(0);
    expect(computeFloatingBottomInset({ anchors: [pill("tall")] })).toBe(0);
    expect(computeFloatingBottomInset({ anchors: [null, undefined] })).toBe(0);
  });
});

describe("computeFloatingBottomInset - the selection pill", () => {
  // The spec's worked example: 16 (space-5, added by the CSS calc) + 54 + 8.
  it("clears the measured pill plus one --space-3 gap", () => {
    expect(computeFloatingBottomInset({ anchors: [pill(54)] })).toBe(62);
  });

  it("tracks a measured height rather than assuming the fallback", () => {
    // The pill wraps on coarse pointers and grows; a constant would overlap.
    expect(computeFloatingBottomInset({ anchors: [pill(96)] })).toBe(104);
    expect(computeFloatingBottomInset({ anchors: [pill(40)] })).toBe(48);
  });

  it("uses the tallest visible anchor, not the sum", () => {
    const inset = computeFloatingBottomInset({
      anchors: [pill(54), pill(80), pill(30)],
    });
    expect(inset).toBe(88);
  });

  it("honours a custom gap", () => {
    expect(computeFloatingBottomInset({ anchors: [pill(50)], gap: 0 })).toBe(
      50,
    );
  });
});

describe("computeFloatingBottomInset - narrowOnly anchors", () => {
  // Above 600px the breadcrumb is bottom-LEFT, outside the centred card's
  // footprint, so it must not push the stack up.
  it("excludes a narrowOnly anchor on a wide viewport", () => {
    expect(
      computeFloatingBottomInset({
        anchors: [breadcrumb(28)],
        narrow: false,
      }),
    ).toBe(0);
  });

  it("includes a narrowOnly anchor on a narrow viewport", () => {
    expect(
      computeFloatingBottomInset({ anchors: [breadcrumb(28)], narrow: true }),
    ).toBe(36);
  });

  it("takes the max of pill and breadcrumb when narrow", () => {
    expect(
      computeFloatingBottomInset({
        anchors: [pill(54), breadcrumb(28)],
        narrow: true,
      }),
    ).toBe(62);
    expect(
      computeFloatingBottomInset({
        anchors: [pill(20), breadcrumb(40)],
        narrow: true,
      }),
    ).toBe(48);
  });

  it("falls back to the pill alone when the breadcrumb is hidden", () => {
    expect(
      computeFloatingBottomInset({
        anchors: [pill(54), breadcrumb(28, false)],
        narrow: true,
      }),
    ).toBe(62);
  });
});

describe("toPx", () => {
  it("formats a rounded pixel string", () => {
    expect(toPx(62)).toBe("62px");
    expect(toPx(61.4)).toBe("61px");
    expect(toPx(61.6)).toBe("62px");
  });

  it("never emits a negative or NaN length", () => {
    expect(toPx(-5)).toBe("0px");
    expect(toPx(NaN)).toBe("0px");
    expect(toPx(undefined)).toBe("0px");
  });
});

describe("constants", () => {
  it("uses --space-3 (8px) as the gap", () => {
    expect(FLOATING_BOTTOM_GAP_PX).toBe(8);
  });

  it("keeps the pill fallback as a first-frame value only", () => {
    // Documented as a measured current value, not a design token.
    expect(SELBAR_FALLBACK_H_PX).toBe(56);
  });
});
