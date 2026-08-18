import { describe, expect, it } from "vitest";
import {
  fetchPriorityForThumbnail,
  loadingModeForThumbnail,
  thumbnailRequestWindow,
} from "./thumbnailRequestPriority.js";

describe("thumbnailRequestPriority", () => {
  it("keeps the viewport eager and gives only its first square row high priority", () => {
    const bounds = thumbnailRequestWindow({
      visibleStart: 12,
      visibleEnd: 20,
      renderStart: 8,
      renderEnd: 24,
      columns: 4,
    });

    expect(bounds).toEqual({
      eagerStart: 12,
      eagerEnd: 20,
      highStart: 12,
      highEnd: 16,
    });
    expect(loadingModeForThumbnail(10, bounds)).toBe("lazy");
    expect(fetchPriorityForThumbnail(10, bounds)).toBe("auto");
    expect(loadingModeForThumbnail(12, bounds)).toBe("eager");
    expect(fetchPriorityForThumbnail(12, bounds)).toBe("high");
    expect(loadingModeForThumbnail(17, bounds)).toBe("eager");
    expect(fetchPriorityForThumbnail(17, bounds)).toBe("auto");
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

  it("uses packed row boundaries in justified mode", () => {
    const bounds = thumbnailRequestWindow({
      visibleStart: 3,
      visibleEnd: 10,
      renderStart: 0,
      renderEnd: 10,
      columns: 4,
      rowStarts: [0, 3, 7, 10],
    });

    expect(bounds.highEnd).toBe(7);
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
