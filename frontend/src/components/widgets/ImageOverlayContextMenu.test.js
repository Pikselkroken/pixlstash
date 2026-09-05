// Overlay (lightbox) context menu - the ImageOverlay right-click menu that
// reuses ImageGridContextMenu in `overlay-mode`.
//
// ImageGrid.vue (~7k lines) and ImageOverlay.vue (~5k lines) are impractical to
// mount, so - following the ImageGridLockBadge.test.js precedent - these tests
// exercise the exact contracts the feature relies on:
//
//   1. ImageGridContextMenu in overlay-mode renders ONLY the restricted overlay
//      action set (and, in scrapheap view, Save picture + Restore + Delete
//      forever), hiding all grid-only actions. Its Delete is scoped by the
//      `selectedImageIds` prop.
//   2. The overlay's media-area right-click guard: a contextmenu over the media
//      canvas opens the custom menu; over a text/sidebar panel it does NOT
//      (native menu preserved for copy/paste/spellcheck).
//   3. The delete-scoping contract in ImageGrid.deleteSelected(idsOverride):
//      an overlay delete targets ONLY the overlay picture and never mutates the
//      grid selection (reproduced verbatim from the refactored handler).

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false), // real ref so the menu template unwraps it
    onSessionReset: () => () => {},
  };
});

import ImageGridContextMenu from "./ImageGridContextMenu.vue";

const REQUIRED = {
  allPicturesId: "ALL",
  unassignedPicturesId: "UNASSIGNED",
  scrapheapPicturesId: "SCRAPHEAP",
  backendUrl: "http://x",
};

// v-icon isn't registered in the test app; stub it so the menu mounts. The menu
// teleports to <body>, so stub teleport to render its content inline where the
// wrapper can query it.
const globalStubs = {
  global: { stubs: { "v-icon": true, teleport: true } },
};

function itemLabels(wrapper) {
  return wrapper
    .findAll("button.ctx-item")
    .map((b) => {
      const shortcut = b.find(".ctx-shortcut");
      const suffix = shortcut.exists() ? shortcut.text() : "";
      return b
        .text()
        .replace(suffix, "")
        .replace(/\s+/g, " ")
        .trim();
    })
    .filter(Boolean);
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("overlay-mode context menu - action set", () => {
  it("renders the normal-view overlay actions and hides grid-only ones", () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: {
          id: 42,
          format: "jpg",
          faces: [],
          mediaKind: "picture",
          copyAvailable: true,
        },
      },
      ...globalStubs,
    });

    const labels = itemLabels(wrapper).join(" | ");

    // The overlay set (find-similar-faces only appears when faces exist).
    expect(labels).toContain("Save picture");
    expect(labels).toContain("Save picture as…");
    expect(labels).toContain("Copy picture");
    expect(labels).toContain("Share picture");
    expect(labels).toContain("Reverse image search");
    expect(labels).toContain("Segment");
    expect(labels).toContain("Restore from snapshot");
    expect(labels).toMatch(/(^|\| )Delete($| \|)/);

    // Grid-only actions must NOT be present.
    expect(labels).not.toContain("Tag");
    expect(labels).not.toContain("Stack");
    expect(labels).not.toContain("Edit with ComfyUI");
    expect(labels).not.toContain("Filters");
    // The add-to-entity controls (project/character/set) are not rendered.
    expect(wrapper.findComponent({ name: "AddToEntityControl" }).exists()).toBe(
      false,
    );

    // Dark-surface skin is applied.
    expect(wrapper.find(".image-ctx-menu").classes()).toContain(
      "image-ctx-menu--on-dark",
    );
  });

  it("shows Find similar faces only when the picture has faces", () => {
    const withFaces = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: {
          id: 42,
          format: "jpg",
          faces: [{ id: 7, frame_index: 0, bbox: [0, 0, 1, 1] }],
        },
      },
      ...globalStubs,
    });
    expect(itemLabels(withFaces).join(" ")).toContain("Find similar faces");
  });

  it("keeps Save picture alongside Restore + Delete forever in scrapheap view", () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "SCRAPHEAP", // matches scrapheapPicturesId
        contextImage: {
          id: 42,
          format: "jpg",
          faces: [],
          mediaKind: "picture",
          copyAvailable: true,
        },
      },
      ...globalStubs,
    });
    const labels = itemLabels(wrapper);
    expect(labels).toContain("Save picture");
    expect(labels).toContain("Save picture as…");
    expect(labels).toContain("Copy picture");
    expect(labels).toContain("Restore");
    expect(labels).toContain("Delete forever");
    expect(labels).not.toContain("Share picture");
    expect(labels).not.toContain("Segment");
  });

  it("Save picture emits the overlay-scoped save action", async () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: {
          id: 42,
          format: "jpg",
          faces: [],
          mediaKind: "picture",
          copyAvailable: true,
        },
      },
      ...globalStubs,
    });

    const save = wrapper
      .findAll("button.ctx-item")
      .find((b) => b.text().includes("Save picture"));
    expect(save).toBeTruthy();
    await save.trigger("click");

    expect(wrapper.emitted("save-picture")).toBeTruthy();
  });

  it("renders the extraction group first, with semantic separators and adaptive video labels", () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: {
          id: 42,
          format: "mp4",
          mediaKind: "video",
          copyAvailable: true,
          faces: [],
        },
      },
      ...globalStubs,
    });

    expect(itemLabels(wrapper).slice(0, 4)).toEqual([
      "Save video",
      "Save video as…",
      "Copy current frame",
      "Share picture",
    ]);
    expect(wrapper.findAll('[role="separator"]').length).toBeGreaterThanOrEqual(2);
  });

  it("exposes accelerator hints and routes them through the same menu actions", async () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: {
          id: 42,
          format: "jpg",
          mediaKind: "picture",
          copyAvailable: true,
          faces: [],
        },
      },
      ...globalStubs,
    });
    const menu = wrapper.find('[role="menu"]');
    await menu.trigger("keydown", { key: "s", ctrlKey: true });
    expect(wrapper.emitted("save-picture")).toHaveLength(1);

    await wrapper.setProps({ visible: true });
    await menu.trigger("keydown", { key: "c", ctrlKey: true });
    expect(wrapper.emitted("copy-picture")).toHaveLength(1);
    expect(wrapper.find('[aria-keyshortcuts="Control+S"]').exists()).toBe(true);
    expect(wrapper.find('[aria-keyshortcuts="Control+C"]').exists()).toBe(true);
  });

  it("keeps unavailable Copy visible, disabled, and explains why", () => {
    const reason = "The picture is still loading and cannot be copied yet.";
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: {
          id: 42,
          format: "jpg",
          mediaKind: "picture",
          copyAvailable: false,
          copyUnavailableReason: reason,
          faces: [],
        },
      },
      ...globalStubs,
    });
    const copy = wrapper
      .findAll("button.ctx-item")
      .find((button) => button.text().includes("Copy picture"));
    expect(copy.attributes("disabled")).toBeDefined();
    expect(copy.attributes("title")).toBe(reason);
    const reasonId = copy.attributes("aria-describedby");
    expect(wrapper.find(`#${reasonId}`).text()).toBe(reason);
  });

  it("Delete emits delete-selected - scoped by the selectedImageIds prop (the overlay picture)", async () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42], // ImageGrid binds this to [overlayImageId]
        selectedCharacter: "ALL",
        contextImage: { id: 42, format: "jpg", faces: [] },
      },
      ...globalStubs,
    });

    const del = wrapper
      .findAll("button.ctx-item")
      .find((b) => b.text().trim() === "Delete");
    expect(del).toBeTruthy();
    await del.trigger("click");

    expect(wrapper.emitted("delete-selected")).toBeTruthy();
    // The menu's target for the action IS its selectedImageIds prop.
    expect(wrapper.props("selectedImageIds")).toEqual([42]);
  });
});

// ── 2. Overlay media-area right-click guard ─────────────────────────────────
// Reproduces the overlay's structure + the @contextmenu handler being bound to
// the media canvas ONLY. A right-click on a sibling sidebar/text panel is never
// seen by the handler, so the native menu is preserved there.
const OverlayCanvasStandin = {
  emits: ["request-context-menu"],
  data() {
    return { hasImage: true };
  },
  methods: {
    handleMediaContextMenu(event) {
      if (!this.hasImage) return; // no image → native menu
      event.preventDefault();
      this.$emit("request-context-menu", {
        clientX: event.clientX,
        clientY: event.clientY,
        image: { id: 42 },
      });
    },
  },
  template: `
    <div class="overlay-main">
      <div class="overlay-canvas" @contextmenu="handleMediaContextMenu">media</div>
      <aside class="overlay-sidebar">
        <textarea class="desc">description text</textarea>
      </aside>
    </div>
  `,
};

describe("overlay right-click target guard", () => {
  it("opens the custom menu over the media canvas", async () => {
    const wrapper = mount(OverlayCanvasStandin);
    await wrapper.find(".overlay-canvas").trigger("contextmenu");
    expect(wrapper.emitted("request-context-menu")).toBeTruthy();
    expect(wrapper.emitted("request-context-menu")[0][0].image.id).toBe(42);
  });

  it("does NOT open the custom menu over a text/sidebar panel", async () => {
    const wrapper = mount(OverlayCanvasStandin);
    await wrapper.find(".overlay-sidebar textarea").trigger("contextmenu");
    expect(wrapper.emitted("request-context-menu")).toBeFalsy();
  });
});

// ── 3. Delete-scoping contract (ImageGrid.deleteSelected(idsOverride)) ───────
// Reproduces the refactored guard verbatim: with an override the delete targets
// exactly those ids and the grid selection is left untouched.
describe("overlay delete scoping contract", () => {
  function simulateDeleteSelected({ idsOverride, gridSelection }) {
    const scoped = Array.isArray(idsOverride) && idsOverride.length > 0;
    const baseIds = scoped ? idsOverride : gridSelection.value;
    const deleted = baseIds.slice(); // what the DELETE request would target
    // Post-delete: removeImagesById drops deleted ids from the selection...
    gridSelection.value = gridSelection.value.filter(
      (id) => !deleted.includes(id),
    );
    // ...and the grid-only selection rewrite is skipped when scoped.
    if (!scoped) {
      gridSelection.value = []; // (stand-in for the grid path's rewrite)
    }
    return deleted;
  }

  it("deletes ONLY the overlay picture and leaves an unrelated grid selection intact", () => {
    const gridSelection = { value: [10, 20, 30] };
    const deleted = simulateDeleteSelected({
      idsOverride: [55], // the overlay picture, not in the grid selection
      gridSelection,
    });
    expect(deleted).toEqual([55]);
    expect(gridSelection.value).toEqual([10, 20, 30]);
  });

  it("removes the overlay picture from the grid selection when it happened to be selected", () => {
    const gridSelection = { value: [10, 55, 30] };
    const deleted = simulateDeleteSelected({
      idsOverride: [55],
      gridSelection,
    });
    expect(deleted).toEqual([55]);
    expect(gridSelection.value).toEqual([10, 30]);
  });

  it("grid path (no override) still acts on the whole grid selection", () => {
    const gridSelection = { value: [10, 20, 30] };
    const deleted = simulateDeleteSelected({
      idsOverride: null,
      gridSelection,
    });
    expect(deleted).toEqual([10, 20, 30]);
  });
});

// ── 4. Overlay search actions must close the lightbox ───────────────────────
// Both overlay search actions put their results in the GRID, which sits behind
// the lightbox - and while the overlay is open every grid mutation is deferred
// (frontend_architecture §9.1). An overlay search handler that does not close
// the overlay therefore looks like it did nothing at all. That was the
// find-similar-faces bug: it was wired to the shared GRID handler, which has no
// closeOverlay() because the grid menu has no overlay to close.
//
// Reproduces both ImageGrid handlers verbatim against a shared fake state.
describe("overlay search actions close the lightbox", () => {
  function makeState() {
    return {
      overlayOpen: true,
      reverseImageSearchPictureIds: [],
      faceLikenessSearchFaceId: null,
      clearSearchEmitted: 0,
    };
  }

  const closeOverlay = (s) => {
    s.overlayOpen = false;
  };

  // ImageGrid.handleOverlayReverseImageSearch
  function overlayReverseImageSearch(s, overlayPictureId) {
    if (overlayPictureId == null) return;
    s.faceLikenessSearchFaceId = null;
    s.reverseImageSearchPictureIds = [overlayPictureId];
    closeOverlay(s);
    s.clearSearchEmitted += 1;
  }

  // ImageGrid.handleOverlayFindSimilarFaces
  function overlayFindSimilarFaces(s, faceId) {
    if (!faceId) return;
    s.reverseImageSearchPictureIds = [];
    s.faceLikenessSearchFaceId = faceId;
    closeOverlay(s);
    s.clearSearchEmitted += 1;
  }

  // ImageGrid.handleFindSimilarFaces - the GRID handler, kept for the grid menu.
  // No closeOverlay: there is no overlay open on that path.
  function gridFindSimilarFaces(s, faceId) {
    if (!faceId) return;
    s.reverseImageSearchPictureIds = [];
    s.faceLikenessSearchFaceId = faceId;
    s.clearSearchEmitted += 1;
  }

  it("find-similar-faces closes the overlay and arms the face search", () => {
    const s = makeState();
    overlayFindSimilarFaces(s, 7);
    expect(s.overlayOpen).toBe(false);
    expect(s.faceLikenessSearchFaceId).toBe(7);
    expect(s.reverseImageSearchPictureIds).toEqual([]);
    expect(s.clearSearchEmitted).toBe(1);
  });

  it("matches its reverse-image-search sibling's close behaviour", () => {
    const faces = makeState();
    const reverse = makeState();
    overlayFindSimilarFaces(faces, 7);
    overlayReverseImageSearch(reverse, 42);
    expect(faces.overlayOpen).toBe(reverse.overlayOpen);
    expect(faces.clearSearchEmitted).toBe(reverse.clearSearchEmitted);
  });

  it("the two searches are mutually exclusive", () => {
    const s = makeState();
    overlayReverseImageSearch(s, 42);
    expect(s.reverseImageSearchPictureIds).toEqual([42]);
    expect(s.faceLikenessSearchFaceId).toBe(null);

    overlayFindSimilarFaces(s, 7);
    expect(s.faceLikenessSearchFaceId).toBe(7);
    expect(s.reverseImageSearchPictureIds).toEqual([]);
  });

  it("ignores a missing face id (no close, no search)", () => {
    const s = makeState();
    overlayFindSimilarFaces(s, undefined);
    expect(s.overlayOpen).toBe(true);
    expect(s.faceLikenessSearchFaceId).toBe(null);
    expect(s.clearSearchEmitted).toBe(0);
  });

  it("the grid handler still does not close anything (unchanged)", () => {
    const s = makeState();
    s.overlayOpen = false; // grid menu: no lightbox in the first place
    gridFindSimilarFaces(s, 7);
    expect(s.faceLikenessSearchFaceId).toBe(7);
    expect(s.clearSearchEmitted).toBe(1);
  });
});

// ── 5. The overlay menu emits the face id the handler needs ─────────────────
// The handler bails on a falsy faceId, so the menu must pass one. With a single
// face the item acts directly; with several it opens a per-face submenu.
describe("overlay-mode find-similar-faces payload", () => {
  const faceMenu = (faces) =>
    mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: { id: 42, format: "jpg", faces },
      },
      ...globalStubs,
    });

  it("emits the only face's id when the picture has one face", async () => {
    const wrapper = faceMenu([{ id: 7, frame_index: 0, bbox: [0, 0, 1, 1] }]);
    const item = wrapper
      .findAll("button.ctx-item")
      .find((b) => b.text().includes("Find similar faces"));
    await item.trigger("click");
    expect(wrapper.emitted("find-similar-faces")[0]).toEqual([7]);
  });

  it("emits the chosen face's id from the multi-face submenu", async () => {
    const wrapper = faceMenu([
      { id: 7, frame_index: 0, bbox: [0, 0, 1, 1] },
      { id: 9, frame_index: 0, bbox: [1, 1, 2, 2] },
    ]);
    await wrapper.find(".ctx-submenu-wrap").trigger("mouseenter");
    const faceItems = wrapper.findAll(".ctx-face-item");
    expect(faceItems.length).toBe(2);
    await faceItems[1].trigger("click");
    expect(wrapper.emitted("find-similar-faces")[0]).toEqual([9]);
  });

  it("skips faces from later video frames and faces with no id", async () => {
    const wrapper = faceMenu([
      { id: null, frame_index: 0, bbox: [0, 0, 1, 1] },
      { id: 11, frame_index: 3, bbox: [1, 1, 2, 2] },
    ]);
    expect(itemLabels(wrapper).join(" ")).not.toContain("Find similar faces");
  });
});
