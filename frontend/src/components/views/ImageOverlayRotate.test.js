// Rotate in place, from the lightbox.
//
// The design is deliberately unguarded: one click, or one `[` / `]`, is one 90°
// step applied immediately, with no dialog and no confirmation. That puts the
// weight on three things this file asserts, because there is no second chance
// to catch them:
//
//   * the control sends the direction it is labelled with;
//   * the shortcut is silent while the user is typing in the caption or tag
//     field, where a bracket is a bracket;
//   * a format that cannot carry a rotation is greyed with the reason, never
//     silently switched to making a copy.
//
// The fourth is the one most likely to ship broken, and it is worse here than
// on the grid. A rotate rewrites the file's EXIF orientation tag and leaves
// every pixel alone, so `pixel_sha` - the sampled content hash the media URL's
// `?v=` was built from - does NOT move. The browser applies the orientation tag
// itself, so it would go on painting the bytes it already decoded, at a URL
// nothing had changed. `orientation` is therefore part of the buster now, the
// same decision the thumbnail token makes on the server; the last describe
// block is what holds it.

import { afterEach, describe, it, expect, beforeEach, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

// Statically imported for the same reason ImageOverlayUndo.test.js gives:
// `vi.mock` is hoisted anyway, and a lazy import only moves the cost of
// compiling this SFC inside the first test's timeout.
import ImageOverlay from "./ImageOverlay.vue";

enableAutoUnmount(afterEach);

// What the metadata endpoint currently reports about the picture's bytes. A
// rotate moves `orientation` and nothing else - `pixel_sha` stays put, which is
// exactly why it cannot be the whole cache-buster.
let metadataPixelSha = "sha-stable";
let metadataOrientation = 1;

const getMock = vi.fn(async (url) => {
  const path = String(url ?? "");
  if (path.includes("/workflow")) {
    const e = new Error("no workflow");
    e.response = { status: 404 };
    throw e;
  }
  if (path.includes("/metadata")) {
    const id = Number(path.match(/\/pictures\/(\d+)\//)?.[1] ?? 7);
    return {
      data: {
        id,
        format: "jpg",
        pixel_sha: metadataPixelSha,
        // Only picture 7 is ever turned; a neighbour stays upright.
        orientation: id === 7 ? metadataOrientation : 1,
        width: 1600,
        height: 1200,
        tags: [],
      },
    };
  }
  return { data: [] };
});
const postMock = vi.fn(async () => ({ data: {} }));

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: {
      get: (...a) => getMock(...a),
      post: (...a) => postMock(...a),
      delete: vi.fn(),
    },
    appendShareToken: (u) => u,
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
  };
});

vi.mock("../../api/operations", () => ({
  listOperations: vi.fn().mockResolvedValue([]),
  getUndoState: vi.fn().mockResolvedValue({ can_undo: true, can_redo: true }),
  undoLastOperation: vi.fn().mockResolvedValue({ operations: [] }),
  redoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoBatch: vi.fn().mockResolvedValue({ operations: [] }),
}));

const STUBS = {
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: true,
  OverlayMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  "v-icon": true,
  "v-menu": true,
  // Renders its activator slot, because the rotate buttons live inside one and
  // an inert stub would make every assertion below pass against nothing.
  "v-tooltip": {
    props: ["text", "disabled", "location"],
    template: "<div><slot name='activator' :props='{}' /></div>",
  },
};

const flush = () => new Promise((r) => setTimeout(r, 0));

async function openOverlay(
  image = { id: 7, format: "jpg", tags: [] },
  allImages = [image],
) {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: image.id,
      allImages,
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
    },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
  await wrapper.setProps({ open: true });
  await flush();
  await flush();
  return wrapper;
}

function press(key, init = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  (init.target ?? window).dispatchEvent(event);
  return event;
}

/** The rotate calls that actually reached the wire, oldest first. */
function rotateCalls() {
  return postMock.mock.calls
    .filter(([url]) => String(url ?? "").includes("/pictures/rotate"))
    .map(([, body]) => body);
}

function rotateButtons(wrapper) {
  const buttons = wrapper.findAll("button");
  return {
    left: buttons.find((b) =>
      (b.attributes("aria-label") || "").startsWith("Rotate left"),
    ),
    right: buttons.find((b) =>
      (b.attributes("aria-label") || "").startsWith("Rotate right"),
    ),
    // A greyed control keeps the refusal as its accessible name, so find those
    // by their shortcut hint instead of by a label that is no longer "Rotate…".
    byShortcut: (key) =>
      buttons.find((b) => b.attributes("aria-keyshortcuts") === key),
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  metadataPixelSha = "sha-stable";
  metadataOrientation = 1;
  getMock.mockClear();
  postMock.mockClear();
  postMock.mockImplementation(async () => ({
    data: {
      rotated_picture_ids: [7],
      unsupported_picture_ids: [],
      skipped_picture_ids: [],
    },
  }));
});

describe("ImageOverlay - the rotate buttons", () => {
  it("sends the direction the button is labelled with, with no dialog", async () => {
    const wrapper = await openOverlay();
    const { left, right } = rotateButtons(wrapper);
    expect(left, "the rotate-left button is not rendered").toBeTruthy();

    await left.trigger("click");
    await flush();
    await right.trigger("click");
    await flush();

    expect(rotateCalls()).toEqual([
      { picture_ids: [7], direction: "ccw" },
      { picture_ids: [7], direction: "cw" },
    ]);
    // No confirmation stood between the click and the request: both landed.
    expect(wrapper.findAll(".v-dialog").length).toBe(0);
  });

  it("greys the control on a format that cannot carry a rotation", async () => {
    const wrapper = await openOverlay({ id: 7, format: "webp", tags: [] });
    const { byShortcut } = rotateButtons(wrapper);
    const left = byShortcut("[");

    expect(left.attributes("disabled")).toBeDefined();
    // …and says why, pointing at the route that still works rather than
    // silently making a copy.
    expect(left.attributes("aria-label")).toContain("PNG and JPEG only");
    expect(left.attributes("aria-label")).toContain("Filters > Rotate");

    await left.trigger("click");
    await flush();
    expect(rotateCalls()).toEqual([]);
  });

  it("greys the control on a reference-folder file", async () => {
    const wrapper = await openOverlay({
      id: 7,
      format: "jpg",
      reference_folder_id: 3,
      tags: [],
    });
    const left = rotateButtons(wrapper).byShortcut("[");
    expect(left.attributes("disabled")).toBeDefined();
    expect(left.attributes("aria-label")).toContain("Reference-folder");
  });
});

describe("ImageOverlay - the [ and ] shortcuts", () => {
  it("rotates on a bare bracket", async () => {
    await openOverlay();

    const event = press("[");
    await flush();
    expect(event.defaultPrevented).toBe(true);
    expect(rotateCalls()).toEqual([{ picture_ids: [7], direction: "ccw" }]);

    press("]");
    await flush();
    expect(rotateCalls()[1]).toEqual({ picture_ids: [7], direction: "cw" });
  });

  it("keeps the second of two rapid presses, in order", async () => {
    // Two presses IS the 180° gesture, so a busy guard that dropped the second
    // one would quietly halve the action. They also must not overlap: each
    // request reads the current orientation and writes the next, so two in
    // flight over one picture lose a turn between them.
    let inFlight = 0;
    let overlapped = false;
    postMock.mockImplementation(async () => {
      inFlight += 1;
      if (inFlight > 1) overlapped = true;
      await new Promise((r) => setTimeout(r, 5));
      inFlight -= 1;
      return {
        data: {
          rotated_picture_ids: [7],
          unsupported_picture_ids: [],
          skipped_picture_ids: [],
        },
      };
    });
    const wrapper = await openOverlay();

    // No await between them: the second lands while the first is still open.
    const first = press("]");
    const second = press("]");
    expect(first.defaultPrevented && second.defaultPrevented).toBe(true);
    await new Promise((r) => setTimeout(r, 40));

    expect(rotateCalls()).toEqual([
      { picture_ids: [7], direction: "cw" },
      { picture_ids: [7], direction: "cw" },
    ]);
    expect(overlapped, "two rotates were in flight at once").toBe(false);
    wrapper.unmount();
  });

  it("stays out of the way while the user is typing", async () => {
    // A bracket is an ordinary character in a caption or a tag. Turning the
    // picture instead of typing it would be an edit the user never asked for,
    // in the one place they cannot see the picture change.
    await openOverlay();

    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    press("[", { target: input });
    await flush();

    expect(rotateCalls()).toEqual([]);
    input.remove();
  });

  it("leaves Ctrl+[ to the browser", async () => {
    // Ctrl/Cmd+[ is Back on several platforms; a bracket that both navigated
    // history and turned the picture is the worst kind of collision.
    await openOverlay();
    const event = press("[", { ctrlKey: true });
    await flush();
    expect(rotateCalls()).toEqual([]);
    expect(event.defaultPrevented).toBe(false);
  });

  it("does not fire on a format that cannot rotate", async () => {
    await openOverlay({ id: 7, format: "webp", tags: [] });
    press("[");
    await flush();
    expect(rotateCalls()).toEqual([]);
  });
});

describe("ImageOverlay - the picture on screen after a rotate", () => {
  it("reads the picture's text again, whose word boxes the turn invalidated", async () => {
    await openOverlay();
    const textReads = () =>
      getMock.mock.calls.filter(([url]) =>
        String(url ?? "").startsWith("/pictures/7/text"),
      ).length;
    const before = textReads();
    expect(before).toBeGreaterThan(0);

    press("]");
    await flush();
    await flush();
    expect(rotateCalls().length).toBe(1);
    expect(textReads()).toBe(before + 1);
  });

  it("re-requests the file when 180° leaves the pixels alone", async () => {
    // Two presses the same way is 180°. `pixel_sha` never moves across either
    // of them - the pixels are untouched - so if the buster were the sha alone
    // the `<img>` would keep the bytes it decoded before the first press.
    const wrapper = await openOverlay();
    const srcBefore = wrapper.find(".overlay-img").attributes("src");

    metadataOrientation = 6; // one quarter-turn
    press("]");
    await flush();
    await flush();
    const srcQuarter = wrapper.find(".overlay-img").attributes("src");

    metadataOrientation = 3; // …and the second, i.e. 180°
    press("]");
    await flush();
    await flush();

    const srcAfter = wrapper.find(".overlay-img").attributes("src");
    expect(rotateCalls().length).toBe(2);
    expect(srcQuarter).not.toBe(srcBefore);
    expect(srcAfter).not.toBe(srcQuarter);
    expect(srcAfter).not.toBe(srcBefore);
    // The orientation is the only thing carrying the change into the URL - the
    // content hash is identical on both sides of the turn and is not in it.
    expect(srcAfter).toContain("?v=o3");
    expect(srcAfter).not.toContain("sha-stable");
  });

  it("stays turned when the grid hands back its list after the rotate", async () => {
    // `overlay-change` makes the grid re-read the tile and push a new
    // `allImages`. The overlay re-resolves the open picture from the snapshot it
    // froze on open, which still carries the pre-rotate orientation; letting
    // that win put the old `?v=` back and the browser repainted the old bytes.
    const wrapper = await openOverlay();

    metadataOrientation = 6;
    press("]");
    await flush();
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toContain("?v=o6");

    await wrapper.setProps({
      allImages: [{ id: 7, format: "jpg", orientation: 6, tags: [] }],
    });
    await flush();
    await flush();

    expect(wrapper.find(".overlay-img").attributes("src")).toContain("?v=o6");
  });

  it("stays turned when the refreshed row is not from the frozen snapshot", async () => {
    // An expanded stack member is resolved from the filmstrip's own rows, which
    // the snapshot patch never reaches. Same shape here: the picture is absent
    // from the snapshot, so the grid's stale row re-resolves it directly.
    const wrapper = await openOverlay({ id: 7, format: "jpg", tags: [] }, []);

    metadataOrientation = 6;
    press("]");
    await flush();
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toContain("?v=o6");

    await wrapper.setProps({
      allImages: [{ id: 7, format: "jpg", orientation: 1, tags: [] }],
    });
    await flush();
    await flush();

    expect(wrapper.find(".overlay-img").attributes("src")).toContain("?v=o6");
  });

  it("stays turned after moving to a neighbour and back", async () => {
    // Coming back re-resolves the picture from the frozen snapshot too, and a
    // row with no orientation there would pin the bare, unrotated URL.
    const seven = { id: 7, format: "jpg", tags: [] };
    const wrapper = await openOverlay(seven, [
      seven,
      { id: 8, format: "jpg", tags: [] },
    ]);

    metadataOrientation = 6;
    press("]");
    await flush();
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toContain("?v=o6");

    await wrapper.setProps({ initialImageId: 8 });
    await flush();
    await flush();
    await wrapper.setProps({ initialImageId: 7 });
    // Right away, before the metadata read lands: no flash of the old file.
    expect(wrapper.find(".overlay-img").attributes("src")).toContain("?v=o6");
    await flush();
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toContain("?v=o6");
  });

  it("unpins the URL when the first known orientation comes from rotate", async () => {
    const defaultGetMock = getMock.getMockImplementation();
    let metadataCalls = 0;
    getMock.mockImplementation(async (url) => {
      const path = String(url ?? "");
      if (path.includes("/workflow")) {
        const e = new Error("no workflow");
        e.response = { status: 404 };
        throw e;
      }
      if (path.includes("/metadata")) {
        metadataCalls += 1;
        if (metadataCalls === 1) {
          return new Promise(() => {});
        }
        return {
          data: {
            id: 7,
            format: "jpg",
            pixel_sha: metadataPixelSha,
            orientation: 8,
            width: 1600,
            height: 1200,
            tags: [],
          },
        };
      }
      return { data: [] };
    });
    try {
      const wrapper = await openOverlay();
      expect(wrapper.find(".overlay-img").attributes("src")).toBe(
        "http://test/pictures/7.jpg",
      );

      press("]");
      await flush();
      await flush();

      expect(wrapper.find(".overlay-img").attributes("src")).toBe(
        "http://test/pictures/7.jpg?v=o8",
      );
    } finally {
      getMock.mockImplementation(defaultGetMock);
    }
  });

  it("leaves an unrotated picture's URL exactly as it was", async () => {
    // Orientation 1 contributes nothing, so a picture that has never been
    // turned keeps the bare URL - the same one `prefetchFullImage` and the
    // neighbour preloads warm from a grid record. A version that grew a suffix
    // for every picture would hand the HTTP cache a URL it has never seen the
    // day this ships, and one that disagreed with those builders would read a
    // stale prefetch back out of the memory cache.
    const wrapper = await openOverlay();
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toBe(
      "http://test/pictures/7.jpg",
    );
  });

  it("tells the grid its bytes changed, so the tile re-reads", async () => {
    const wrapper = await openOverlay();
    press("]");
    await flush();
    await flush();

    const changes = wrapper.emitted("overlay-change") ?? [];
    expect(changes.length).toBe(1);
    expect(changes[0][0]).toEqual({ imageId: 7, fields: { pixels: true } });
  });

  it("says nothing to the grid when the server rotated nothing", async () => {
    postMock.mockImplementation(async () => ({
      data: {
        rotated_picture_ids: [],
        unsupported_picture_ids: [],
        skipped_picture_ids: [7],
      },
    }));
    const wrapper = await openOverlay();
    press("]");
    await flush();
    await flush();

    expect(wrapper.emitted("overlay-change")).toBeUndefined();
  });
});
