// Lightbox context menu - the Lightbox right-click menu that
// reuses ImageGridContextMenu in `lightbox-mode`.
//
// ImageGrid.vue (~7k lines) and Lightbox.vue (~5k lines) are impractical to
// mount, so - following the ImageGridLockBadge.test.js precedent - these tests
// exercise the exact contracts the feature relies on:
//
//   1. ImageGridContextMenu in lightbox-mode renders ONLY the restricted lightbox
//      action set (and, in scrapheap view, Save picture + Restore + Delete
//      forever), hiding all grid-only actions. Its Delete is scoped by the
//      `selectedImageIds` prop.
//   2. The lightbox's media-area right-click guard: a contextmenu over the media
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

// The tooltip is stubbed to carry its text on the element it wraps or sits in:
// the real one renders nothing until it opens, and needs Vuetify besides.
const TooltipStub = {
  name: "Tooltip",
  props: ["text", "activator"],
  template:
    '<span class="tip" :data-text="text"><slot name="activator" :props="{}" /></span>',
};

/** The tooltip text a control carries, whether the tip sits inside or around it. */
function tip(wrapper) {
  const inner = wrapper.find(".tip");
  if (inner.exists()) return inner.attributes("data-text");
  return wrapper.element.parentElement?.dataset.text;
}

// v-icon isn't registered in the test app; stub it so the menu mounts. The menu
// teleports to <body>, so stub teleport to render its content inline where the
// wrapper can query it.
const globalStubs = {
  global: { stubs: { "v-icon": true, Tooltip: TooltipStub, teleport: true } },
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

describe("lightbox-mode context menu - action set", () => {
  it("renders the normal-view lightbox actions and hides grid-only ones", () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        lightboxMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        // The ComfyUI entry is fenced on this, as in the grid menu.
        comfyuiConfigured: true,
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
    expect(labels).not.toContain("Filters");
    // Lightbox mode DOES offer the picture as a workflow's input (#1406), and
    // it is the only ComfyUI entry here: the grid menu's batch "Filters" and
    // the tag/stack entries stay out.
    expect(labels).toContain("Use as input for…");
    // The add-to-entity controls (project/character/set) are not rendered.
    expect(wrapper.findComponent({ name: "AddToEntityControl" }).exists()).toBe(
      false,
    );

    // Dark-surface skin is applied.
    expect(wrapper.find(".image-ctx-menu").classes()).toContain(
      "ctx-menu--on-dark",
    );
  });

  it("does not offer a workflow's input with no ComfyUI configured", () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        lightboxMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        comfyuiConfigured: false,
        contextImage: { id: 42, format: "jpg", faces: [] },
      },
      ...globalStubs,
    });
    expect(itemLabels(wrapper).join(" | ")).not.toContain("Use as input for…");
  });

  it("emits the picture's id when the input entry is chosen", async () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        lightboxMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        comfyuiConfigured: true,
        contextImage: { id: 42, format: "jpg", faces: [] },
      },
      ...globalStubs,
    });
    const entry = wrapper
      .findAll("button.ctx-item")
      .find((b) => b.text().includes("Use as input for"));
    expect(entry).toBeTruthy();
    await entry.trigger("click");
    // `delegateWith` closes first, then emits on the next tick.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.emitted("use-as-input")).toEqual([[42]]);
  });

  it("shows Find similar faces only when the picture has faces", () => {
    const withFaces = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        lightboxMode: true,
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
        lightboxMode: true,
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

  it("Save picture emits the lightbox-scoped save action", async () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        lightboxMode: true,
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
        lightboxMode: true,
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
        lightboxMode: true,
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
        lightboxMode: true,
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
    expect(tip(copy)).toBe(reason);
    const reasonId = copy.attributes("aria-describedby");
    expect(wrapper.find(`#${reasonId}`).text()).toBe(reason);
  });

  it("Delete emits delete-selected - scoped by the selectedImageIds prop (the lightbox picture)", async () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        lightboxMode: true,
        visible: true,
        selectedImageIds: [42], // ImageGrid binds this to [lightboxImageId]
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

// ── 2. Lightbox media-area right-click guard ─────────────────────────────────
// Reproduces the lightbox's structure + the @contextmenu handler being bound to
// the media canvas ONLY. A right-click on a sibling sidebar/text panel is never
// seen by the handler, so the native menu is preserved there.
const LightboxCanvasStandin = {
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
    <div class="lightbox-main">
      <div class="lightbox-canvas" @contextmenu="handleMediaContextMenu">media</div>
      <aside class="lightbox-sidebar">
        <textarea class="desc">description text</textarea>
      </aside>
    </div>
  `,
};

describe("lightbox right-click target guard", () => {
  it("opens the custom menu over the media canvas", async () => {
    const wrapper = mount(LightboxCanvasStandin);
    await wrapper.find(".lightbox-canvas").trigger("contextmenu");
    expect(wrapper.emitted("request-context-menu")).toBeTruthy();
    expect(wrapper.emitted("request-context-menu")[0][0].image.id).toBe(42);
  });

  it("does NOT open the custom menu over a text/sidebar panel", async () => {
    const wrapper = mount(LightboxCanvasStandin);
    await wrapper.find(".lightbox-sidebar textarea").trigger("contextmenu");
    expect(wrapper.emitted("request-context-menu")).toBeFalsy();
  });
});

// ── 3. Delete-scoping contract (ImageGrid.deleteSelected(idsOverride)) ───────
// Reproduces the refactored guard verbatim: with an override the delete targets
// exactly those ids and the grid selection is left untouched.
describe("lightbox delete scoping contract", () => {
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
// the lightbox - and while the lightbox is open every grid mutation is deferred
// (frontend_architecture §9.1). An overlay search handler that does not close
// the lightbox therefore looks like it did nothing at all. That was the
// find-similar-faces bug: it was wired to the shared GRID handler, which has no
// closeLightbox() because the grid menu has no lightbox to close.
//
// Reproduces both ImageGrid handlers verbatim against a shared fake state.
describe("overlay search actions close the lightbox", () => {
  function makeState() {
    return {
      lightboxOpen: true,
      reverseImageSearchPictureIds: [],
      faceLikenessSearchFaceId: null,
      clearSearchEmitted: 0,
    };
  }

  const closeLightbox = (s) => {
    s.lightboxOpen = false;
  };

  // ImageGrid.handleLightboxReverseImageSearch
  function lightboxReverseImageSearch(s, lightboxPictureId) {
    if (lightboxPictureId == null) return;
    s.faceLikenessSearchFaceId = null;
    s.reverseImageSearchPictureIds = [lightboxPictureId];
    closeLightbox(s);
    s.clearSearchEmitted += 1;
  }

  // ImageGrid.handleLightboxFindSimilarFaces
  function lightboxFindSimilarFaces(s, faceId) {
    if (!faceId) return;
    s.reverseImageSearchPictureIds = [];
    s.faceLikenessSearchFaceId = faceId;
    closeLightbox(s);
    s.clearSearchEmitted += 1;
  }

  // ImageGrid.handleFindSimilarFaces - the GRID handler, kept for the grid menu.
  // No closeLightbox: there is no lightbox open on that path.
  function gridFindSimilarFaces(s, faceId) {
    if (!faceId) return;
    s.reverseImageSearchPictureIds = [];
    s.faceLikenessSearchFaceId = faceId;
    s.clearSearchEmitted += 1;
  }

  it("find-similar-faces closes the overlay and arms the face search", () => {
    const s = makeState();
    lightboxFindSimilarFaces(s, 7);
    expect(s.lightboxOpen).toBe(false);
    expect(s.faceLikenessSearchFaceId).toBe(7);
    expect(s.reverseImageSearchPictureIds).toEqual([]);
    expect(s.clearSearchEmitted).toBe(1);
  });

  it("matches its reverse-image-search sibling's close behaviour", () => {
    const faces = makeState();
    const reverse = makeState();
    lightboxFindSimilarFaces(faces, 7);
    lightboxReverseImageSearch(reverse, 42);
    expect(faces.lightboxOpen).toBe(reverse.lightboxOpen);
    expect(faces.clearSearchEmitted).toBe(reverse.clearSearchEmitted);
  });

  it("the two searches are mutually exclusive", () => {
    const s = makeState();
    lightboxReverseImageSearch(s, 42);
    expect(s.reverseImageSearchPictureIds).toEqual([42]);
    expect(s.faceLikenessSearchFaceId).toBe(null);

    lightboxFindSimilarFaces(s, 7);
    expect(s.faceLikenessSearchFaceId).toBe(7);
    expect(s.reverseImageSearchPictureIds).toEqual([]);
  });

  it("ignores a missing face id (no close, no search)", () => {
    const s = makeState();
    lightboxFindSimilarFaces(s, undefined);
    expect(s.lightboxOpen).toBe(true);
    expect(s.faceLikenessSearchFaceId).toBe(null);
    expect(s.clearSearchEmitted).toBe(0);
  });

  it("the grid handler still does not close anything (unchanged)", () => {
    const s = makeState();
    s.lightboxOpen = false; // grid menu: no lightbox in the first place
    gridFindSimilarFaces(s, 7);
    expect(s.faceLikenessSearchFaceId).toBe(7);
    expect(s.clearSearchEmitted).toBe(1);
  });
});

// ── 5. The overlay menu emits the face id the handler needs ─────────────────
// The handler bails on a falsy faceId, so the menu must pass one. With a single
// face the item acts directly; with several it opens a per-face submenu.
describe("lightbox-mode find-similar-faces payload", () => {
  const faceMenu = (faces) =>
    mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        lightboxMode: true,
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
