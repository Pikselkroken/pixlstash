import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isOwnShareCopyDrop,
  outgoingDragIds,
  resetShareDragForTests,
  runShareDrag,
} from "./shareDrag.js";
import {
  extractSupportedImportFilesFromDataTransfer,
  isInternalImageDrag,
  isPictureDrag,
  readInternalDragPayload,
} from "./media.js";

const BATCH = "/home/me/.config/pixlstash-desktop/share-copies/0123abcd";
const COPY = `${BATCH}/a.png`;

function fileDrop(...paths) {
  const files = paths.map((path) => {
    const file = new File(["x"], path.split("/").pop(), { type: "image/png" });
    file.testPath = path;
    return file;
  });
  return { types: ["Files"], files, items: [], getData: () => "" };
}
// What dragover sees: the type, but the files themselves are protected.
const dragOver = () => ({ types: ["Files"], files: [], getData: () => "" });

// The OS drag stays in flight until this resolves, as startDrag does on Linux.
// It "takes" a second, so the drag counts as run to its end.
let finishDrag;
let now = 0;
beforeEach(() => {
  resetShareDragForTests();
  now = 0;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  window.pixlstashDesktop = {
    startShareDrag: vi.fn(
      () =>
        new Promise(
          (resolve) =>
            (finishDrag = () => {
              now += 1000;
              resolve({});
            }),
        ),
    ),
    getDroppedFilePath: (file) => file.testPath,
  };
});
afterEach(() => {
  vi.restoreAllMocks();
  delete window.pixlstashDesktop;
});

describe("the in-app targets take a drag-out (#2420)", () => {
  it("is a picture drag of the dragged ids while in flight, and nothing after", async () => {
    expect(outgoingDragIds(dragOver())).toBe(null);
    const onEnd = vi.fn();
    const done = runShareDrag([7, 8], [COPY], { onEnd });

    expect(isPictureDrag(dragOver())).toBe(true);
    expect(isInternalImageDrag(dragOver())).toBe(true);
    expect(readInternalDragPayload(fileDrop(COPY))).toEqual({
      type: "image-ids",
      imageIds: [7, 8],
    });

    finishDrag();
    await done;
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(isPictureDrag(dragOver())).toBe(false);
    expect(readInternalDragPayload(fileDrop(COPY))).toBe(null);
  });

  it("never files the dragged pictures for a drop of someone else's file", () => {
    void runShareDrag([7], [COPY]);
    const foreign = fileDrop("/home/me/Pictures/a.png");
    expect(outgoingDragIds(foreign)).toBe(null);
    expect(isInternalImageDrag(foreign)).toBe(false);
    expect(readInternalDragPayload(foreign)).toBe(null);
  });

  it("outlives a startDrag that returns at once, until the drag is plainly over", async () => {
    // macOS: startDrag returns before the drag ends.
    window.pixlstashDesktop.startShareDrag = vi.fn().mockResolvedValue({});
    const onEnd = vi.fn();
    await runShareDrag([7], [COPY], { onEnd });
    expect(isPictureDrag(dragOver())).toBe(true);
    // No pointer events reach the page during an OS drag; a held button is not the end either.
    window.dispatchEvent(Object.assign(new Event("pointermove"), { buttons: 1 }));
    expect(onEnd).not.toHaveBeenCalled();
    window.dispatchEvent(Object.assign(new Event("pointermove"), { buttons: 0 }));
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(isPictureDrag(dragOver())).toBe(false);
  });

  it("ends a fast-returning drag after a drop on the window, once the drop is handled", async () => {
    window.pixlstashDesktop.startShareDrag = vi.fn().mockResolvedValue({});
    await runShareDrag([7], [COPY]);
    window.dispatchEvent(new Event("drop"));
    expect(isPictureDrag(dragOver())).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(isPictureDrag(dragOver())).toBe(false);
  });

  it("matches the copy even when main reports the root through a symlink", () => {
    void runShareDrag([7], [COPY]);
    expect(outgoingDragIds(fileDrop("/data/elsewhere/0123abcd/a.png"))).toEqual([7]);
  });

  it("prefers the JSON payload of an ordinary in-app drag", () => {
    void runShareDrag([7], [COPY]);
    const json = { types: ["application/json"], files: [], getData: () => '{"type":"face-bbox"}' };
    expect(readInternalDragPayload(json)).toEqual({ type: "face-bbox" });
  });
});

describe("a drag-out's copies dropped back never import", () => {
  it("is recognised by the files the drag handed out, and only those", async () => {
    expect(isOwnShareCopyDrop(fileDrop(COPY))).toBe(false);
    const done = runShareDrag([7], [COPY]);
    finishDrag();
    await done;
    expect(isOwnShareCopyDrop(fileDrop(COPY))).toBe(true);
    expect(isOwnShareCopyDrop(fileDrop("/home/me/Pictures/a.png"))).toBe(false);
  });

  it("still imports the owner's own file dropped together with a copy", async () => {
    const done = runShareDrag([7], [COPY]);
    finishDrag();
    await done;
    const mixed = fileDrop(COPY, "/home/me/Pictures/new.png");
    expect(isOwnShareCopyDrop(mixed)).toBe(false);
    expect(await extractSupportedImportFilesFromDataTransfer(mixed)).toHaveLength(2);
  });

  it("reaches no importer, while a real file still does", async () => {
    const outside = fileDrop("/home/me/Pictures/a.png");
    const done = runShareDrag([7], [COPY]);
    finishDrag();
    await done;
    expect(await extractSupportedImportFilesFromDataTransfer(fileDrop(COPY))).toEqual([]);
    expect(await extractSupportedImportFilesFromDataTransfer(outside)).toHaveLength(1);
  });
});
