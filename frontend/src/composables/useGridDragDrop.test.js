// A Ctrl-drag (Option on a Mac) of pictures in the desktop app is an OS file drag of share copies
// (#2420); a plain drag, and any drag in a browser, stays the HTML5 drag with
// the JSON payload.

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
import { resetShareDragForTests } from "../utils/shareDrag.js";
import { useNoticeStore } from "../stores/useNoticeStore";
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

function dragStart({ ctrlKey = true, altKey = false } = {}) {
  const data = new Map();
  return {
    ctrlKey,
    altKey,
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
  it("drags the whole selection's original files, not the thumbnail", async () => {
    const g = grid();
    const event = dragStart();
    g.handleContainerDragStart({ id: 1 }, event);
    expect(event.defaultPrevented).toBe(true);
    expect(event.dataTransfer.data.size).toBe(0);
    expect(g.dragSourceImageIds.value).toEqual(new Set(["1", "2"]));
    await flush();
    expect(makeShareCopies).toHaveBeenCalledWith([1, 2], { link_originals: true, file_names: "made" });
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

  it("drags any number of pictures as files", async () => {
    const ids = Array.from({ length: 5000 }, (_, i) => i + 1);
    const g = grid(ids);
    const event = dragStart();
    g.handleContainerDragStart({ id: 1 }, event);
    expect(event.defaultPrevented).toBe(true);
    await flush();
    expect(makeShareCopies).toHaveBeenCalledWith(ids, { link_originals: true, file_names: "made" });
  });

  it("starts nothing when the button was let go before the copies were ready", async () => {
    const g = grid();
    g.handleContainerDragStart({ id: 1 }, dragStart());
    window.dispatchEvent(new Event("mouseup"));
    await flush();
    expect(startShareDrag).not.toHaveBeenCalled();
    expect(g.dragSource.value).toBe(null);
  });

  it("stays the in-app HTML5 drag in a browser or a read-only session", () => {
    const html5 = (g, id = 1) => {
      const event = dragStart();
      g.handleContainerDragStart({ id }, event);
      expect(event.defaultPrevented).toBe(false);
      expect(event.dataTransfer.getData("application/json")).toContain("image-ids");
    };
    isReadOnly.value = true;
    html5(grid());
    isReadOnly.value = false;
    delete window.pixlstashDesktop;
    html5(grid());
    expect(makeShareCopies).not.toHaveBeenCalled();
  });

  it("stays the instant in-app drag in the desktop app without Ctrl", () => {
    const g = grid();
    const event = dragStart({ ctrlKey: false });
    g.handleContainerDragStart({ id: 1 }, event);
    expect(event.defaultPrevented).toBe(false);
    expect(event.dataTransfer.getData("application/json")).toContain("image-ids");
    expect(g.dragSourceImageIds.value).toEqual(new Set(["1", "2"]));
    g.handleThumbnailNativeDragStart({ id: 1 }, dragStart({ ctrlKey: false }));
    expect(makeShareCopies).not.toHaveBeenCalled();
    expect(startShareDrag).not.toHaveBeenCalled();
  });

  it("takes Alt for the drag-out only on a Mac, where Ctrl+click is a right click", async () => {
    const altOnly = { ctrlKey: false, altKey: true };
    const event = dragStart(altOnly);
    grid().handleContainerDragStart({ id: 1 }, event);
    expect(event.defaultPrevented).toBe(false);
    expect(makeShareCopies).not.toHaveBeenCalled();

    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    );
    const ctrlOnMac = dragStart();
    grid().handleContainerDragStart({ id: 1 }, ctrlOnMac);
    expect(ctrlOnMac.defaultPrevented).toBe(false);
    const optionOnMac = dragStart(altOnly);
    grid().handleContainerDragStart({ id: 1 }, optionOnMac);
    expect(optionOnMac.defaultPrevented).toBe(true);
    await flush();
    expect(makeShareCopies).toHaveBeenCalledTimes(1);
  });

  describe("the hint to hold Ctrl", () => {
    const dragEnd = (clientX, clientY, dropEffect = "none") => ({
      clientX,
      clientY,
      dataTransfer: { dropEffect },
    });
    const hints = () =>
      useNoticeStore().notices.filter((n) => n.key === "drag-out-hint");
    function plainDrag() {
      const g = grid();
      g.handleContainerDragStart({ id: 1 }, dragStart({ ctrlKey: false }));
      return g;
    }

    it("shows once when a plain drag is let go outside the window", () => {
      const g = plainDrag();
      const info = vi.spyOn(useNoticeStore(), "info");
      g.handleDragEnd(dragEnd(-40, 200));
      // The tile's own dragend follows its container's.
      g.handleDragEnd(dragEnd(-40, 200));
      expect(info).toHaveBeenCalledTimes(1);
      expect(hints()).toHaveLength(1);
      expect(hints()[0].text).toContain("hold Ctrl");
      expect(g.dragSource.value).toBe(null);
    });

    it("stays quiet for a drop inside the window, or one taken outside", () => {
      plainDrag().handleDragEnd(dragEnd(100, 100));
      plainDrag().handleDragEnd(dragEnd(window.innerWidth + 5, 100, "copy"));
      expect(hints()).toHaveLength(0);
    });

    it("stays quiet where the key would not drag out", () => {
      delete window.pixlstashDesktop;
      plainDrag().handleDragEnd(dragEnd(-40, 200));
      expect(hints()).toHaveLength(0);
    });
  });
});

describe("a drop on the grid that imports nothing", () => {
  /** A drop event: `types` as the browser reports them, `files` as File objects. */
  function drop(types, files = []) {
    return {
      dataTransfer: {
        types,
        files,
        items: files.map((file) => ({ kind: "file", getAsFile: () => file })),
        getData: () => "",
      },
    };
  }

  it("says the drop carried no files when it held only text", async () => {
    // A huge selection dragged out of Nemo on X11 arrives as this.
    delete window.pixlstashDesktop;
    await grid().handleGridDrop(drop(["text/plain"]));
    const last = useNoticeStore().notices.at(-1);
    expect(last.text).toContain("carried no files");
    expect(last.text).not.toContain("supported");
  });

  it("still says the files are unsupported when it held only unsupported files", async () => {
    delete window.pixlstashDesktop;
    const event = drop(["Files"], [
      new File(["x"], "notes.psd", { type: "image/vnd.adobe.photoshop" }),
    ]);
    const done = grid().handleGridDrop(event);
    // The browser empties the payload once dispatch is over: a check made
    // after the walk's await would read no types and blame a missing file.
    event.dataTransfer.types = [];
    await done;
    const last = useNoticeStore().notices.at(-1);
    expect(last.text).toContain("None of those files are a supported");
  });
});
