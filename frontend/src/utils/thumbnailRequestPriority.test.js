import { describe, expect, it } from "vitest";
import {
  fetchPriorityForThumbnail,
  loadingModeForThumbnail,
  thumbnailRequestWindow,
} from "./thumbnailRequestPriority.js";

describe("thumbnailRequestPriority", () => {
  it("eagerly prefetches the rendered buffer while prioritizing the visible window", () => {
    const bounds = thumbnailRequestWindow({
      visibleStart: 12,
      visibleEnd: 20,
      renderStart: 8,
      renderEnd: 24,
      columns: 4,
    });

    expect(bounds).toEqual({
      eagerStart: 8,
      eagerEnd: 24,
      highStart: 12,
      highEnd: 20,
    });
    expect(loadingModeForThumbnail(10, bounds)).toBe("eager");
    expect(fetchPriorityForThumbnail(10, bounds)).toBe("auto");
    expect(loadingModeForThumbnail(12, bounds)).toBe("eager");
    expect(fetchPriorityForThumbnail(12, bounds)).toBe("high");
    expect(loadingModeForThumbnail(17, bounds)).toBe("eager");
    expect(fetchPriorityForThumbnail(17, bounds)).toBe("high");
    expect(fetchPriorityForThumbnail(21, bounds)).toBe("auto");
    expect(loadingModeForThumbnail(24, bounds)).toBe("lazy");
  });

  it("uses the render window until the virtualizer reports a viewport", () => {
    expect(
      thumbnailRequestWindow({
        visibleStart: 0,
        visibleEnd: 0,
        renderStart: 0,
        renderEnd: 12,
        columns: 4,
      }),
    ).toEqual({
      eagerStart: 0,
      eagerEnd: 12,
      highStart: 0,
      highEnd: 4,
    });
  });

  it("uses the first packed row as the fallback priority band", () => {
    const bounds = thumbnailRequestWindow({
      visibleStart: 0,
      visibleEnd: 0,
      renderStart: 0,
      renderEnd: 10,
      columns: 4,
      rowStarts: [0, 3, 7, 10],
    });

    expect(bounds.highEnd).toBe(3);
  });

  it("keeps a representative 100-item scroll buffer eager without raising its priority", () => {
    const bounds = thumbnailRequestWindow({
      visibleStart: 0,
      visibleEnd: 24,
      renderStart: 0,
      renderEnd: 124,
      columns: 6,
    });
    const counts = { eager: 0, lazy: 0, high: 0, auto: 0 };

    for (let index = 0; index < 124; index += 1) {
      counts[loadingModeForThumbnail(index, bounds)] += 1;
      counts[fetchPriorityForThumbnail(index, bounds)] += 1;
    }

    expect(counts).toEqual({ eager: 124, lazy: 0, high: 24, auto: 100 });
  });

  it("treats invalid indexes as non-critical", () => {
    const bounds = thumbnailRequestWindow({
      visibleStart: 0,
      visibleEnd: 4,
      renderStart: 0,
      renderEnd: 8,
      columns: 4,
    });

    expect(loadingModeForThumbnail(undefined, bounds)).toBe("lazy");
    expect(fetchPriorityForThumbnail(undefined, bounds)).toBe("auto");
  });
});
