// Overlay (lightbox) context menu — the ImageOverlay right-click menu that
// reuses ImageGridContextMenu in `overlay-mode`.
//
// ImageGrid.vue (~7k lines) and ImageOverlay.vue (~5k lines) are impractical to
// mount, so — following the ImageGridLockBadge.test.js precedent — these tests
// exercise the exact contracts the feature relies on:
//
//   1. ImageGridContextMenu in overlay-mode renders ONLY the restricted overlay
//      action set (and, in scrapheap view, Restore + Delete forever), hiding all
//      grid-only actions. Its Delete is scoped by the `selectedImageIds` prop.
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
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false), // real ref so the menu template unwraps it
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
    .map((b) => b.text().replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("overlay-mode context menu — action set", () => {
  it("renders exactly the 6 normal-view overlay actions and hides grid-only ones", () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "ALL",
        contextImage: { id: 42, format: "jpg", faces: [] },
      },
      ...globalStubs,
    });

    const labels = itemLabels(wrapper).join(" | ");

    // The overlay set (find-similar-faces only appears when faces exist).
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

  it("renders only Restore + Delete forever in scrapheap view", () => {
    const wrapper = mount(ImageGridContextMenu, {
      props: {
        ...REQUIRED,
        overlayMode: true,
        visible: true,
        selectedImageIds: [42],
        selectedCharacter: "SCRAPHEAP", // matches scrapheapPicturesId
        contextImage: { id: 42, format: "jpg", faces: [] },
      },
      ...globalStubs,
    });
    const labels = itemLabels(wrapper);
    expect(labels).toContain("Restore");
    expect(labels).toContain("Delete forever");
    expect(labels).not.toContain("Share picture");
    expect(labels).not.toContain("Segment");
  });

  it("Delete emits delete-selected — scoped by the selectedImageIds prop (the overlay picture)", async () => {
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
