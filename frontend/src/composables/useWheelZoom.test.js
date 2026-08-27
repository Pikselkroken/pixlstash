// The zoom family's stateful core. The pure arithmetic is pinned in
// utils/zoomMath.test.js; these tests pin the GLUE: the fit measurement and
// entry state, the floor-policy dispatch (rest vs exit+hysteresis), the pan
// clamp, and the settle window that feeds the aria announcer.

import { describe, it, expect, vi, afterEach } from "vitest";
import { useWheelZoom, ZOOM_SETTLE_MS } from "./useWheelZoom";
import { ZOOM_EXIT_GESTURE_GAP_MS, ZOOM_MAX_SCALE } from "../utils/zoomMath";

// 800×600 viewport over a 1600×1200 original: fit = 0.5, a clean floor.
function measured(zoom) {
  zoom.setMeasurements({
    containerWidth: 800,
    containerHeight: 600,
    naturalWidth: 1600,
    naturalHeight: 1200,
  });
  return zoom;
}

function wheel(zoom, deltaY, cursor = { x: 400, y: 300 }) {
  return zoom.wheelZoom({ deltaY, deltaMode: 0 }, cursor);
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useWheelZoom - entry and measurement", () => {
  it("enters at fit, centred, and reports the fit percentage", () => {
    const zoom = measured(useWheelZoom());
    expect(zoom.scale.value).toBe(0.5);
    expect(zoom.atFit.value).toBe(true);
    expect(zoom.offset.value).toEqual({ x: 0, y: 0 });
    expect(zoom.percentLabel.value).toBe("50%");
  });

  it("does nothing before measurement - no wheel, no snap, empty label", () => {
    const zoom = useWheelZoom();
    expect(wheel(zoom, -100)).toBe(false);
    zoom.toggleSnap();
    expect(zoom.scale.value).toBeNull();
    expect(zoom.percentLabel.value).toBe("");
  });

  it("a re-measure keeps a fit-parked scale ON the new fit", () => {
    const zoom = measured(useWheelZoom());
    zoom.setMeasurements({
      containerWidth: 400,
      containerHeight: 600,
      naturalWidth: 1600,
      naturalHeight: 1200,
    });
    expect(zoom.fitScale.value).toBe(0.25);
    expect(zoom.scale.value).toBe(0.25);
    expect(zoom.atFit.value).toBe(true);
  });

  it("a re-measure keeps a zoomed scale and re-clamps it to the new floor", () => {
    const zoom = measured(useWheelZoom());
    zoom.snapTo(1);
    zoom.setMeasurements({
      containerWidth: 800,
      containerHeight: 600,
      naturalWidth: 640,
      naturalHeight: 480,
    });
    // New image fits at 1.25 > the kept scale 1 → clamped up to the floor.
    expect(zoom.fitScale.value).toBe(1.25);
    expect(zoom.scale.value).toBe(1.25);
  });

  it("the effective ceiling is max(maxScale, fitScale) - a tiny image whose fit exceeds 800% opens legally", () => {
    const zoom = useWheelZoom();
    zoom.setMeasurements({
      containerWidth: 800,
      containerHeight: 600,
      naturalWidth: 64,
      naturalHeight: 48,
    });
    expect(zoom.fitScale.value).toBeGreaterThan(ZOOM_MAX_SCALE);
    expect(zoom.scale.value).toBe(zoom.fitScale.value);
    expect(zoom.effectiveMaxScale.value).toBe(zoom.fitScale.value);
    // Neither direction can leave the (degenerate) continuum.
    wheel(zoom, -500);
    expect(zoom.scale.value).toBe(zoom.fitScale.value);
    wheel(zoom, 500);
    expect(zoom.scale.value).toBe(zoom.fitScale.value);
  });
});

describe("useWheelZoom - floor policy", () => {
  it("rest: a big out-wheel at the floor rests at fit and never exits", () => {
    const onExit = vi.fn();
    const zoom = measured(useWheelZoom({ floorPolicy: "rest", onExit }));
    for (let i = 0; i < 10; i += 1) wheel(zoom, 500);
    expect(zoom.scale.value).toBe(0.5);
    expect(onExit).not.toHaveBeenCalled();
  });

  it("exit: three accumulated notches of resistance at the floor call onExit", () => {
    const onExit = vi.fn();
    const zoom = measured(useWheelZoom({ floorPolicy: "exit", onExit }));
    wheel(zoom, 120); // one notch at the floor: not enough
    wheel(zoom, 120); // two: still resisting
    expect(onExit).not.toHaveBeenCalled();
    wheel(zoom, 120); // the third completes the resistance
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("exit: zooming back in clears the accumulated crumbs", () => {
    const onExit = vi.fn();
    const zoom = measured(useWheelZoom({ floorPolicy: "exit", onExit }));
    wheel(zoom, 240); // two notches at the floor
    wheel(zoom, -100); // leaves the floor → accumulator resets
    wheel(zoom, 500); // lands back on the floor (clamped), no exit yet
    expect(onExit).not.toHaveBeenCalled();
  });

  it("exit: a pause longer than the gesture gap restarts the accumulation", () => {
    vi.useFakeTimers();
    const onExit = vi.fn();
    const zoom = measured(useWheelZoom({ floorPolicy: "exit", onExit }));
    wheel(zoom, 240); // two notches, then the user stops
    vi.advanceTimersByTime(ZOOM_EXIT_GESTURE_GAP_MS + 1);
    wheel(zoom, 240); // a NEW gesture: must meet the resistance itself
    expect(onExit).not.toHaveBeenCalled();
    wheel(zoom, 120); // same gesture completes it
    expect(onExit).toHaveBeenCalledTimes(1);
  });
});

describe("useWheelZoom - pan clamp", () => {
  it("clamps the drag so the image edge never crosses the viewport edge", () => {
    const zoom = measured(useWheelZoom());
    zoom.snapTo(1); // 1600×1200 shown in 800×600 → range ±400/±300
    zoom.panBy(10000, -10000);
    expect(zoom.offset.value).toEqual({ x: 400, y: -300 });
  });

  it("re-clamps on zoom-out so the image re-centres at fit", () => {
    const zoom = measured(useWheelZoom());
    zoom.snapTo(1);
    zoom.panBy(400, 300);
    zoom.snapTo(zoom.fitScale.value);
    expect(zoom.offset.value).toEqual({ x: 0, y: 0 });
  });

  it("does not pan at fit - the range is zero", () => {
    const zoom = measured(useWheelZoom());
    zoom.panBy(50, 50);
    expect(zoom.offset.value).toEqual({ x: 0, y: 0 });
  });
});

describe("useWheelZoom - the settle announcer", () => {
  it("announces a wheel gesture once, 500 ms after the last change", () => {
    vi.useFakeTimers();
    const zoom = measured(useWheelZoom());
    wheel(zoom, -100);
    wheel(zoom, -100);
    expect(zoom.announcement.value).toBe("");
    vi.advanceTimersByTime(ZOOM_SETTLE_MS - 1);
    expect(zoom.announcement.value).toBe("");
    vi.advanceTimersByTime(1);
    expect(zoom.announcement.value).toBe(
      `Zoom ${Math.round(0.5 * Math.exp(0.4) * 100)}%`,
    );
  });

  it("announces a snap immediately, and names fit", () => {
    vi.useFakeTimers();
    const zoom = measured(useWheelZoom());
    zoom.snapTo(1);
    expect(zoom.announcement.value).toBe("Zoom 100%");
    zoom.snapTo(zoom.fitScale.value);
    expect(zoom.announcement.value).toBe("Zoom fit, 50%");
  });

  it("a snap cancels a pending wheel settle - one announcement, not two", () => {
    vi.useFakeTimers();
    const zoom = measured(useWheelZoom());
    wheel(zoom, -100);
    zoom.snapTo(1);
    expect(zoom.announcement.value).toBe("Zoom 100%");
    vi.advanceTimersByTime(ZOOM_SETTLE_MS * 2);
    expect(zoom.announcement.value).toBe("Zoom 100%");
  });
});

describe("useWheelZoom - snap toggle", () => {
  it("toggles fit → 100% → fit", () => {
    const zoom = measured(useWheelZoom());
    zoom.toggleSnap();
    expect(zoom.scale.value).toBe(1);
    zoom.toggleSnap();
    expect(zoom.scale.value).toBe(0.5);
  });

  it("from an intermediate scale the toggle goes to fit", () => {
    const zoom = measured(useWheelZoom());
    wheel(zoom, -100);
    expect(zoom.atFit.value).toBe(false);
    zoom.toggleSnap();
    expect(zoom.scale.value).toBe(0.5);
  });

  it("anchors the 100% snap at a given point (the double-click contract)", () => {
    const zoom = measured(useWheelZoom());
    // Double-click at the top-left corner of the viewport: the image point
    // there must stay under that corner through the fit → 100% jump.
    const anchor = { x: 0, y: 0 };
    zoom.toggleSnap(anchor);
    expect(zoom.scale.value).toBe(1);
    // At fit the corner showed image point (-800, -600) from centre (natural
    // px). At 100% that point renders at centre + offset + point → for it to
    // stay at the corner the offset must be the full +range on both axes.
    expect(zoom.offset.value).toEqual({ x: 400, y: 300 });
  });
});
