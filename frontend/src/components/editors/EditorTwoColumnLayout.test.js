// The two-column editor layout (CharacterEditor, PictureSetEditor).
//
// The layout itself is CSS and belongs in a browser, but the branch that picks
// it is logic, and this is what guards it: which width the dialog asks for,
// whether the split class and the second column are rendered, and which rows
// opt out of the columns by spanning. Without these, inverting `isExisting` to
// a constant leaves every other editor test green.
//
// The close-transition case is the subtle one. Hosts null `character` in the
// same tick they set `open` false (`SideBar.closeCharacterEditor`) while
// Vuetify keeps the body mounted for the leave animation, so a width computed
// straight off the prop would snap 720 → 480 mid-exit.

import { describe, it, expect, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  appendShareToken: (url) => url,
  apiClient: { post: vi.fn(), patch: vi.fn() },
}));

vi.mock("../../api/characters", () => ({
  createCharacter: vi.fn(),
  patchCharacter: vi.fn(),
  getReferencePictures: vi.fn().mockResolvedValue({ reference_picture_ids: [] }),
}));

vi.mock("../../api/pictures", () => ({
  listPicturesByIds: vi.fn().mockResolvedValue([]),
}));

vi.mock("../../api/pictureSets", () => ({
  createPictureSet: vi.fn(),
  patchPictureSet: vi.fn(),
}));

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import { getReferencePictures } from "../../api/characters";
import { listPicturesByIds } from "../../api/pictures";
import CharacterEditor from "./CharacterEditor.vue";
import PictureSetEditor from "./PictureSetEditor.vue";

// Renders the default slot only, and exposes the width the editor asked for.
const AppDialog = {
  name: "AppDialog",
  props: ["open", "title", "width"],
  template: `<div class="app-dialog-stub"><slot /><slot name="footer" /></div>`,
};

const stubs = {
  AppDialog,
  AppInput: true,
  AppTextarea: true,
  AppSelect: true,
  AppButton: true,
  FieldLabel: true,
  StarRatingOverlay: true,
  // Rendered as a real element so the spanning class can be read off it, and
  // carrying `data-id` like the sibling stubs in CharacterCreateAndAssign and
  // PictureSetEditor - without it, "the tray is still there" cannot tell a tray
  // pointed at the person being edited from one pointed at `undefined`, which
  // is exactly what a closing dialog would leave.
  AdapterTray: {
    name: "AdapterTray",
    props: ["entityType", "entityId"],
    template: `<div class="adapter-tray-stub" :data-id="entityId ?? ''"></div>`,
  },
};

function mountCharacter(props) {
  setActivePinia(createPinia());
  return mount(CharacterEditor, { props, global: { stubs } });
}

function widthOf(wrapper) {
  return wrapper.findComponent({ name: "AppDialog" }).props("width");
}

describe("CharacterEditor layout", () => {
  it("creating is one column at 480 - both right-column blocks need an id", () => {
    const w = mountCharacter({ open: true, character: null });

    expect(widthOf(w)).toBe(480);
    expect(w.find(".editor-body").classes()).not.toContain("editor-body--split");
    expect(w.findAll(".editor-col")).toHaveLength(1);
  });

  it("editing is two columns at 720 with the tray spanning", () => {
    const w = mountCharacter({ open: true, character: { id: 7, name: "A" } });

    expect(widthOf(w)).toBe(720);
    expect(w.find(".editor-body").classes()).toContain("editor-body--split");
    expect(w.findAll(".editor-col")).toHaveLength(2);
    expect(w.find(".adapter-tray-stub").classes()).toContain("editor-span");
  });

  it("does not reflow or empty itself while closing", async () => {
    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [1] });
    listPicturesByIds.mockResolvedValueOnce([{ id: 1, score: 4 }]);
    const w = mountCharacter({ open: true, character: { id: 7, name: "A" } });
    await flushPromises();
    expect(w.findAll(".ref-picture-item")).toHaveLength(1);

    // Exactly what SideBar.closeCharacterEditor does, in one flush. The body
    // stays mounted for the leave transition, so anything recomputed here is
    // on screen: a 720 → 480 snap, or "No reference images yet" under a person
    // who has them.
    await w.setProps({ open: false, character: null });
    await flushPromises();

    expect(widthOf(w)).toBe(720);
    expect(w.findAll(".editor-col")).toHaveLength(2);
    expect(w.findAll(".ref-picture-item")).toHaveLength(1);
    expect(w.find(".ref-pictures-empty").exists()).toBe(false);
    // The heading and the fields are as visible as the columns are, so they
    // have to hold too - a dialog that says "New person" over four blank
    // fields beside that person's face is the same defect, further up.
    expect(w.findComponent({ name: "AppDialog" }).props("title")).toBe(
      "Edit person",
    );
    expect(w.vm.localCharacter.name).toBe("A");
    // Including the tray, which spans both columns - the widest block in the
    // dialog is the worst one to drop out from under a leave transition. Its
    // id has to hold too: the host nulls the prop, so a tray reading straight
    // off it stays mounted but points at nobody.
    expect(w.find(".adapter-tray-stub").attributes("data-id")).toBe("7");
  });

  it("remounts the tray on each open so a shelf edit in between shows up", async () => {
    const w = mountCharacter({ open: true, character: { id: 7, name: "A" } });
    const firstKey = w.findComponent({ name: "AdapterTray" }).vm.$.vnode.key;

    await w.setProps({ open: false, character: null });
    await w.setProps({ open: true, character: { id: 7, name: "A" } });

    expect(w.findComponent({ name: "AdapterTray" }).vm.$.vnode.key).not.toBe(
      firstKey,
    );
  });

  it("never strands the full-screen reference preview", async () => {
    // The preview is teleported to <body> at z-index 9999 and covers the whole
    // app, and the Escape that dismisses it lives on the dialog's own listener.
    // Ctrl+Enter saves and closes from under an open preview, so a preview that
    // outlives its dialog can only be cleared with a mouse.
    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [1] });
    listPicturesByIds.mockResolvedValueOnce([{ id: 1, score: 4 }]);
    const w = mountCharacter({ open: true, character: { id: 7, name: "A" } });
    await flushPromises();
    // The picture itself now picks the thumbnail; the preview is its own
    // corner control (see CharacterEditorThumbnail.test.js).
    await w.find(".ref-picture-zoom").trigger("click");
    expect(w.vm.previewPic).not.toBeNull();

    await w.setProps({ open: false, character: null });
    expect(w.vm.previewPic).toBeNull();

    // And nothing survives into the next person's dialog either.
    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [] });
    await w.setProps({ open: true, character: { id: 8, name: "B" } });
    expect(w.vm.previewPic).toBeNull();
  });

  it("never paints one person's reference images under another's name", async () => {
    // Person A's read is left in flight while the host swaps to person B, which
    // is one click in the sidebar. A's response then lands last.
    let resolveA;
    getReferencePictures.mockReturnValueOnce(
      new Promise((r) => {
        resolveA = () => r({ reference_picture_ids: [11] });
      }),
    );
    listPicturesByIds.mockResolvedValue([{ id: 11, score: 5 }]);

    const w = mountCharacter({ open: true, character: { id: 1, name: "A" } });

    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [99] });
    listPicturesByIds.mockResolvedValueOnce([{ id: 99, score: 3 }]);
    await w.setProps({ character: { id: 2, name: "B" } });
    await flushPromises();
    expect(w.find(".ref-picture-thumb").attributes("src")).toContain("99");

    resolveA();
    await flushPromises();

    // Still B's picture. A's late response belongs to a person who is no
    // longer on screen, and reference images are that person's face.
    expect(w.find(".ref-picture-thumb").attributes("src")).toContain("99");
  });

  it("survives a person closed and reopened while their read is in flight", async () => {
    // The id is the same on both sides of the round trip, so only request
    // identity distinguishes the abandoned read from the live one.
    let rejectFirst;
    getReferencePictures.mockReturnValueOnce(
      new Promise((_, reject) => {
        rejectFirst = () => reject(new Error("abandoned"));
      }),
    );
    const w = mountCharacter({ open: true, character: { id: 7, name: "A" } });
    await w.setProps({ open: false, character: null });

    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [5] });
    listPicturesByIds.mockResolvedValueOnce([{ id: 5, score: 4 }]);
    await w.setProps({ open: true, character: { id: 7, name: "A" } });
    await flushPromises();
    expect(w.findAll(".ref-picture-item")).toHaveLength(1);

    rejectFirst();
    await flushPromises();

    // The dead read must not wipe the list the reopened dialog already filled.
    expect(w.findAll(".ref-picture-item")).toHaveLength(1);
    expect(w.find(".ref-pictures-empty").exists()).toBe(false);
  });

  it("clears the previous person's images before the next read answers", async () => {
    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [11] });
    listPicturesByIds.mockResolvedValueOnce([{ id: 11, score: 5 }]);
    const w = mountCharacter({ open: true, character: { id: 1, name: "A" } });
    await flushPromises();
    expect(w.findAll(".ref-picture-item")).toHaveLength(1);

    // B's read never answers: nothing of A's may be on screen meanwhile.
    getReferencePictures.mockReturnValueOnce(new Promise(() => {}));
    await w.setProps({ character: { id: 2, name: "B" } });
    await flushPromises();

    expect(w.findAll(".ref-picture-item")).toHaveLength(0);
    // Mid-read is not "none": the empty state would be a claim about B that
    // nothing has answered yet.
    expect(w.find(".ref-pictures-empty").exists()).toBe(false);
  });

  it("ignores an older read that came back empty", async () => {
    // The third write site: A has no reference images, B has some, A answers
    // last. An unguarded empty result wipes B's column.
    let resolveA;
    getReferencePictures.mockReturnValueOnce(
      new Promise((r) => {
        resolveA = () => r({ reference_picture_ids: [] });
      }),
    );
    const w = mountCharacter({ open: true, character: { id: 1, name: "A" } });

    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [99] });
    listPicturesByIds.mockResolvedValueOnce([{ id: 99, score: 3 }]);
    await w.setProps({ character: { id: 2, name: "B" } });
    await flushPromises();
    expect(w.findAll(".ref-picture-item")).toHaveLength(1);

    resolveA();
    await flushPromises();

    expect(w.findAll(".ref-picture-item")).toHaveLength(1);
  });

  it("lets an older read finish without stranding the empty state", async () => {
    // A's read is still in flight when B opens, and B's has not answered. If
    // A's completion were allowed to clear the loading flag, B would render as
    // "No reference images yet" - a claim about B that nothing has made.
    let resolveA;
    getReferencePictures.mockReturnValueOnce(
      new Promise((r) => {
        resolveA = () => r({ reference_picture_ids: [] });
      }),
    );
    const w = mountCharacter({ open: true, character: { id: 1, name: "A" } });

    getReferencePictures.mockReturnValueOnce(new Promise(() => {}));
    await w.setProps({ character: { id: 2, name: "B" } });
    await flushPromises();

    resolveA();
    await flushPromises();

    expect(w.find(".ref-pictures-empty").exists()).toBe(false);
  });

  it("names the reference grid with its own heading", async () => {
    getReferencePictures.mockResolvedValueOnce({ reference_picture_ids: [3] });
    listPicturesByIds.mockResolvedValueOnce([{ id: 3, score: 2 }]);
    const w = mountCharacter({ open: true, character: { id: 7, name: "A" } });
    await flushPromises();

    const headingId = w.find(".ref-pictures-header .section-label").attributes("id");
    expect(headingId).toBeTruthy();
    expect(w.find(".ref-pictures-grid").attributes("aria-labelledby")).toBe(
      headingId,
    );
  });

  it("re-reads the branch on the next open", async () => {
    const w = mountCharacter({ open: true, character: { id: 7, name: "A" } });
    await w.setProps({ open: false, character: null });
    await w.setProps({ open: true });

    expect(widthOf(w)).toBe(480);
    expect(w.findAll(".editor-col")).toHaveLength(1);
  });
});

describe("PictureSetEditor layout", () => {
  it("is two columns at 720, with the appearance row and tray spanning", () => {
    setActivePinia(createPinia());
    const w = mount(PictureSetEditor, {
      props: { open: true, set: { id: 3, name: "Set" } },
      global: { stubs },
    });

    expect(widthOf(w)).toBe(720);
    expect(w.findAll(".editor-col")).toHaveLength(2);
    // The appearance block cannot fit in half a dialog - it has to span, or the
    // 8-column icon grid is what pays for it.
    expect(w.find(".appearance-row").classes()).toContain("editor-span");
    expect(w.find(".adapter-tray-stub").classes()).toContain("editor-span");
  });

  it("does not blank or unlock itself while closing", async () => {
    setActivePinia(createPinia());
    const w = mount(PictureSetEditor, {
      props: { open: true, set: { id: 3, name: "Set", locked: true } },
      global: { stubs },
    });
    expect(w.vm.localSet.name).toBe("Set");

    // SideBar.closeSetEditor, in one flush - the same shape as the person
    // editor's, and the same leave transition still showing two columns.
    await w.setProps({ open: false, set: null });

    expect(w.findComponent({ name: "AppDialog" }).props("title")).toBe(
      "Edit picture set",
    );
    expect(w.vm.localSet.name).toBe("Set");
    // (No column-count assertion here: this editor has no column branch, so it
    // could not fail. The person editor's equivalent does have one, and does.)
    // The wash must not lift either: a locked set flashing editable on its way
    // out is an invitation to type into a dialog that is leaving.
    expect(w.find(".appearance-row").classes()).toContain(
      "appearance-row--locked",
    );
    // And the spanning tray stays, still pointed at the set being edited.
    expect(w.find(".adapter-tray-stub").attributes("data-id")).toBe("3");
  });

  it("remounts its tray on each open", async () => {
    setActivePinia(createPinia());
    const w = mount(PictureSetEditor, {
      props: { open: true, set: { id: 3, name: "Set" } },
      global: { stubs },
    });
    const firstKey = w.findComponent({ name: "AdapterTray" }).vm.$.vnode.key;

    await w.setProps({ open: false, set: null });
    await w.setProps({ open: true, set: { id: 3, name: "Set" } });

    expect(w.findComponent({ name: "AdapterTray" }).vm.$.vnode.key).not.toBe(
      firstKey,
    );
  });

  it("titles itself for the set it was opened on", async () => {
    setActivePinia(createPinia());
    const w = mount(PictureSetEditor, {
      props: { open: true, set: null },
      global: { stubs },
    });
    expect(w.findComponent({ name: "AppDialog" }).props("title")).toBe(
      "New picture set",
    );

    await w.setProps({ open: false });
    await w.setProps({ open: true, set: { id: 3, name: "Set" } });
    expect(w.findComponent({ name: "AppDialog" }).props("title")).toBe(
      "Edit picture set",
    );
  });

  it("keeps the lock wash on the appearance row it now spans", () => {
    setActivePinia(createPinia());
    const w = mount(PictureSetEditor, {
      props: { open: true, set: { id: 3, name: "Set", locked: true } },
      global: { stubs },
    });

    // The wash and its reason belong to `.appearance-row` itself, not to the
    // new spanning wrapper - move them and either the tray falls under the wash
    // or the tooltip's hover target changes.
    const row = w.find(".appearance-row");
    expect(row.classes()).toContain("appearance-row--locked");
    expect(row.attributes("title")).toContain("locked");
    // The per-field disabled state is asserted live in PictureSetEditor.test.js
    // (against a stub that renders it as `data-disabled`, which is what makes
    // that assertion able to fail); it is not repeated here.
  });
});
