// A picture drag in the desktop app is an OS file drag of share copies (#2420);
// in a browser it stays the HTML5 drag with the JSON payload.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import { createPinia, setActivePinia } from "pinia";

vi.mock("../utils/apiClient", async () => {
  const { ref: vueRef } = await import("vue");
  return {
    isReadOnly: vueRef(false),
    apiClient: {},
    API_BASE_URL: "",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
  };
});
vi.mock("../api/pictures", () => ({ makeShareCopies: vi.fn() }));

import { isReadOnly } from "../utils/apiClient";
import { makeShareCopies } from "../api/pictures";
import { MAX_COPY_FILES } from "../utils/copyAsFiles.js";
import { resetShareDragForTests } from "../utils/shareDrag.js";
import { useGridDragDrop } from "./useGridDragDrop.js";

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
let finishDrag;
let startShareDrag;
let now = 0;

function grid(selected = [1, 2]) {
  return useGridDragDrop(
    {
      selectedImageIds: ref(selected),
      touchSelectMode: ref(false),
      imageImporterRef: ref(null),
      thumbnailRefs: {},
      dragPreviewRefs: {},
      prefetchFullImage: () => {},
      reviewOverlayOpen: ref(false),
    },
    {},
  );
}

function dragStart() {
  const data = new Map();
  return {
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    target: null,
    dataTransfer: {
      getData: (type) => data.get(type) || "",
      setData: (type, value) => data.set(type, value),
      setDragImage: () => {},
      data,
    },
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  resetShareDragForTests();
  isReadOnly.value = false;
  makeShareCopies.mockReset();
  makeShareCopies.mockResolvedValue({
    files: [{ path: "/home/me/share-copies/b/1.png" }, { path: "/home/me/share-copies/b/2.png" }],
    skipped: [],
  });
  now = 0;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  // Runs the drag to its end, as startDrag does on Linux and Windows.
  startShareDrag = vi.fn(
    () =>
      new Promise(
        (resolve) =>
          (finishDrag = () => {
            now += 1000;
            resolve({});
          }),
      ),
  );
  window.pixlstashDesktop = { startShareDrag };
});
afterEach(() => {
  vi.restoreAllMocks();
  delete window.pixlstashDesktop;
});

describe("dragging pictures out of the grid (#2420)", () => {
  it("drags the whole selection's share copies as files, not the thumbnail", async () => {
    const g = grid();
    const event = dragStart();
    g.handleContainerDragStart({ id: 1 }, event);
    expect(event.defaultPrevented).toBe(true);
    expect(event.dataTransfer.data.size).toBe(0);
    expect(g.dragSourceImageIds.value).toEqual(new Set(["1", "2"]));
    await flush();
    expect(makeShareCopies).toHaveBeenCalledWith([1, 2], {});
    expect(startShareDrag).toHaveBeenCalledWith(
      ["/home/me/share-copies/b/1.png", "/home/me/share-copies/b/2.png"],
      undefined,
    );
    // The thumbnail's own handler does not start a second drag.
    g.handleThumbnailNativeDragStart({ id: 1 }, event);
    expect(makeShareCopies).toHaveBeenCalledTimes(1);

    finishDrag();
    await flush();
    expect(g.dragSourceImageIds.value.size).toBe(0);
    expect(g.dragSource.value).toBe(null);
  });

  it("starts nothing when the button was let go before the copies were ready", async () => {
    const g = grid();
    g.handleContainerDragStart({ id: 1 }, dragStart());
    window.dispatchEvent(new Event("mouseup"));
    await flush();
    expect(startShareDrag).not.toHaveBeenCalled();
    expect(g.dragSource.value).toBe(null);
  });

  it("stays the in-app HTML5 drag in a browser, read-only, or past the cap", () => {
    const html5 = (g, id = 1) => {
      const event = dragStart();
      g.handleContainerDragStart({ id }, event);
      expect(event.defaultPrevented).toBe(false);
      expect(event.dataTransfer.getData("application/json")).toContain("image-ids");
    };
    isReadOnly.value = true;
    html5(grid());
    isReadOnly.value = false;
    html5(grid(Array.from({ length: MAX_COPY_FILES + 1 }, (_, i) => i + 1)));
    delete window.pixlstashDesktop;
    html5(grid());
    expect(makeShareCopies).not.toHaveBeenCalled();
  });
});
