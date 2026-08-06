// "New person…" from a face row in the lightbox (#645).
//
// The per-face control is AddToEntityControl's single-select `face` mode, so
// the menu itself is covered in AddToEntityControl.test.js. What lives here is
// the part that belongs to the lightbox:
//
//   1. Cancelling the create dialog leaves the face's assignment untouched.
//   2. The Escape ownership. The lightbox's window-level handler closes the
//      lightbox on Escape. With the real AppDialog and the real CharacterEditor
//      in play, Escape must close ONLY the dialog. The body-targeted case is
//      the one that leaked, and the capture-phase guard is what fixes it.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as vuetifyComponents from "vuetify/components";
import * as vuetifyDirectives from "vuetify/directives";

// Both routes answer with CharacterMutationResponse = {status, character}, so
// the record is NESTED. A flat {id, name} mock here is what let the broken
// create-and-assign flow ship green; see CharacterCreateAndAssign.test.js.
vi.mock("../../api/characters", () => ({
  createCharacter: vi.fn().mockResolvedValue({
    status: "success",
    character: { id: 99, name: "Alice" },
  }),
  patchCharacter: vi.fn().mockResolvedValue({
    status: "success",
    character: { id: 99, name: "Alice" },
  }),
  getReferencePictures: vi
    .fn()
    .mockResolvedValue({ reference_picture_ids: [] }),
}));
vi.mock("../../api/pictures", () => ({
  listPicturesByIds: vi.fn().mockResolvedValue([]),
}));
vi.mock("../../utils/apiClient", () => ({
  appendShareToken: (u) => u,
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import CharacterEditor from "../editors/CharacterEditor.vue";

const vuetify = createVuetify({
  components: vuetifyComponents,
  directives: vuetifyDirectives,
});

beforeEach(() => {
  setActivePinia(createPinia());
});

// ── 1. Cancel leaves the assignment untouched ────────────────────────────────

// Reproduced from ImageOverlay: opening the create flow only records the face
// and opens the dialog. Unlike the native <select> it replaced, the menu holds
// no value of its own, so there is nothing to snap back; the face keeps
// whatever it already had until an assignment actually succeeds.
function openCreatePersonForFace(face, query, state) {
  if (!face?.id || state.isReadOnly) return;
  state.createPersonFaceId = face.id;
  state.createPersonFaceKey = face.faceKey;
  state.name = (typeof query === "string" && query.trim()) || "Character 0001";
  state.open = true;
}

function handleCreatePersonClose(state) {
  state.open = false;
  state.createPersonFaceId = null;
}

describe("cancelling the create flow", () => {
  it("leaves the face's assignment exactly as it was", () => {
    const face = {
      id: 4,
      faceKey: 4,
      character_id: 7,
      character_name: "Alice",
    };
    const state = { isReadOnly: false, open: false };
    const before = { ...face };

    openCreatePersonForFace(face, "", state);
    expect(state.open).toBe(true);
    expect(state.createPersonFaceId).toBe(4);

    handleCreatePersonClose(state);
    expect(state.open).toBe(false);
    // No assignment happened, so the face is untouched.
    expect(face).toEqual(before);
    expect(state.createPersonFaceId).toBeNull();
  });

  it("pre-fills the typed query when the search found nobody", () => {
    const state = { isReadOnly: false, open: false };
    openCreatePersonForFace({ id: 4, faceKey: 4 }, "Zed", state);
    expect(state.name).toBe("Zed");
  });

  it("falls back to the next free default name", () => {
    const state = { isReadOnly: false, open: false };
    openCreatePersonForFace({ id: 4, faceKey: 4 }, "  ", state);
    expect(state.name).toBe("Character 0001");
  });

  it("does not open for a face with no id or in a read-only session", () => {
    const state = { isReadOnly: false, open: false };
    openCreatePersonForFace({ id: null, faceKey: "face-0" }, "", state);
    expect(state.open).toBe(false);
    const ro = { isReadOnly: true, open: false };
    openCreatePersonForFace({ id: 4, faceKey: 4 }, "", ro);
    expect(ro.open).toBe(false);
  });
});

// ── 2. Escape closes the dialog, never the lightbox ──────────────────────────

describe("Escape ownership while the person dialog is open", () => {
  let wrapper;
  let lightboxClose;
  let guard;
  let createPersonOpen;

  function pressEscape(target) {
    target.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        bubbles: true,
        cancelable: true,
      }),
    );
  }

  beforeEach(async () => {
    createPersonOpen = { value: true };
    // Stands in for ImageOverlay.handleKeydown: a window-level bubble listener
    // whose Escape branch closes the lightbox.
    lightboxClose = vi.fn();
    const windowHandler = (e) => {
      if (e.key === "Escape") lightboxClose();
    };
    window.addEventListener("keydown", windowHandler);

    // Verbatim ImageOverlay.onCreatePersonKeydownCapture.
    guard = (e) => {
      if (!createPersonOpen.value || e.key !== "Escape") return;
      e.stopImmediatePropagation();
      e.preventDefault();
      createPersonOpen.value = false;
    };
    document.addEventListener("keydown", guard, true);

    wrapper = mount(CharacterEditor, {
      props: {
        open: true,
        character: { id: null, name: "Character 0001" },
        backendUrl: "http://x",
        projects: [],
      },
      global: { plugins: [vuetify] },
      attachTo: document.body,
    });
    await new Promise((r) => setTimeout(r, 30));
    wrapper.__windowHandler = windowHandler;
  });

  afterEach(() => {
    document.removeEventListener("keydown", guard, true);
    window.removeEventListener("keydown", wrapper.__windowHandler);
    wrapper.unmount();
  });

  it("does not reach the lightbox for an Escape from inside the dialog", () => {
    const input = document.querySelector(".app-dialog input");
    expect(input).toBeTruthy();
    pressEscape(input);
    expect(lightboxClose).not.toHaveBeenCalled();
    expect(createPersonOpen.value).toBe(false);
  });

  it("does not reach the lightbox for an Escape targeting the body", () => {
    // The leak this guard exists for: focus outside the dialog subtree means
    // AppDialog's stopPropagation never runs, and CharacterEditor's own
    // document listener would have flipped the flag before a bubble-phase
    // guard could read it. The capture-phase guard runs ahead of both.
    pressEscape(document.body);
    expect(lightboxClose).not.toHaveBeenCalled();
    expect(createPersonOpen.value).toBe(false);
  });

  it("lets Escape through to the lightbox once the dialog is closed", () => {
    createPersonOpen.value = false;
    pressEscape(document.body);
    expect(lightboxClose).toHaveBeenCalled();
  });
});

// ── 3. The leak is real without the guard (characterises the bug) ────────────

describe("without the capture guard", () => {
  it("a body-targeted Escape reaches the lightbox handler", () => {
    const lightboxClose = vi.fn();
    const windowHandler = (e) => {
      if (e.key === "Escape") lightboxClose();
    };
    window.addEventListener("keydown", windowHandler);
    try {
      document.body.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Escape",
          bubbles: true,
          cancelable: true,
        }),
      );
      expect(lightboxClose).toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", windowHandler);
    }
  });
});
