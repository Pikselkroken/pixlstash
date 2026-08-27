// Picking a person's thumbnail off the reference grid (the pin, #character-thumbnail).
//
// The three assertions that matter are about what reaches the PATCH: the key is
// only sent when the user actually picked, `null` is a real value meaning "back
// to the automatic choice", and it is NOT sent when nothing was touched - an
// absent key is what tells the backend to leave an existing pin alone.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as vuetifyComponents from "vuetify/components";
import * as vuetifyDirectives from "vuetify/directives";

vi.mock("../../api/characters", () => ({
  createCharacter: vi.fn(),
  patchCharacter: vi.fn(),
  getReferencePictures: vi
    .fn()
    .mockResolvedValue({ reference_picture_ids: [11, 22] }),
}));
vi.mock("../../api/pictures", () => ({
  listPicturesByIds: vi.fn().mockResolvedValue([
    { id: 11, score: 4 },
    { id: 22, score: 3 },
  ]),
}));
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  appendShareToken: (u) => u,
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { patchCharacter } from "../../api/characters";
import CharacterEditor from "./CharacterEditor.vue";

const vuetify = createVuetify({
  components: vuetifyComponents,
  directives: vuetifyDirectives,
});

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  patchCharacter.mockResolvedValue({
    status: "success",
    character: { id: 99, name: "Alice" },
  });
});

async function openEditor(character) {
  const wrapper = mount(CharacterEditor, {
    props: { open: true, character, backendUrl: "http://x", projects: [] },
    global: {
      plugins: [vuetify],
      // The real AppDialog teleports its body out of the wrapper, so the
      // reference grid would not be findable from here.
      stubs: {
        AppDialog: {
          props: ["open", "title", "width"],
          template: "<div><slot /><slot name='footer' /></div>",
        },
      },
    },
  });
  await flushPromises();
  return wrapper;
}

function pickButtons(wrapper) {
  return wrapper.findAll(".ref-picture-pick");
}

describe("CharacterEditor - thumbnail pin", () => {
  it("marks the pinned reference image and nothing else", async () => {
    const wrapper = await openEditor({
      id: 99,
      name: "Alice",
      thumbnail_picture_id: 22,
    });
    const [first, second] = pickButtons(wrapper);
    expect(first.classes()).not.toContain("ref-picture-pick--selected");
    expect(second.classes()).toContain("ref-picture-pick--selected");
    expect(second.attributes("aria-pressed")).toBe("true");
    expect(wrapper.findAll(".ref-picture-badge")).toHaveLength(1);
  });

  it("PATCHes the clicked picture as the new thumbnail", async () => {
    const wrapper = await openEditor({ id: 99, name: "Alice" });
    await pickButtons(wrapper)[0].trigger("click");
    await wrapper.vm.save();
    await flushPromises();
    expect(patchCharacter.mock.calls[0][1].thumbnail_picture_id).toBe(11);
  });

  it("clears the pin back to automatic when the current one is clicked", async () => {
    const wrapper = await openEditor({
      id: 99,
      name: "Alice",
      thumbnail_picture_id: 22,
    });
    await pickButtons(wrapper)[1].trigger("click");
    await wrapper.vm.save();
    await flushPromises();
    const payload = patchCharacter.mock.calls[0][1];
    expect("thumbnail_picture_id" in payload).toBe(true);
    expect(payload.thumbnail_picture_id).toBeNull();
  });

  it("omits the key entirely when the user did not pick", async () => {
    const wrapper = await openEditor({
      id: 99,
      name: "Alice",
      thumbnail_picture_id: 22,
    });
    await wrapper.vm.save();
    await flushPromises();
    expect("thumbnail_picture_id" in patchCharacter.mock.calls[0][1]).toBe(
      false,
    );
  });

  it("offers a way back when the pin is no longer in the reference list", async () => {
    // The reference list is recomputed from scores, so a pinned picture can
    // drop out of it. The badge is the only control, so without this the pin is
    // invisible AND unclearable.
    const wrapper = await openEditor({
      id: 99,
      name: "Alice",
      thumbnail_picture_id: 4242,
    });
    expect(wrapper.findAll(".ref-picture-badge")).toHaveLength(0);
    const reset = wrapper.find(".ref-pin-reset");
    expect(reset.exists()).toBe(true);

    await reset.trigger("click");
    await wrapper.vm.save();
    await flushPromises();
    expect(patchCharacter.mock.calls[0][1].thumbnail_picture_id).toBeNull();
  });

  it("does not offer the reset while the pin IS in the list", async () => {
    const wrapper = await openEditor({
      id: 99,
      name: "Alice",
      thumbnail_picture_id: 22,
    });
    expect(wrapper.find(".ref-pin-reset").exists()).toBe(false);
  });

  it("matches a pin the server sent as a string", async () => {
    const wrapper = await openEditor({
      id: 99,
      name: "Alice",
      thumbnail_picture_id: "22",
    });
    expect(pickButtons(wrapper)[1].classes()).toContain(
      "ref-picture-pick--selected",
    );
    // And it counts as unchanged, so the key stays out of the PATCH.
    await wrapper.vm.save();
    await flushPromises();
    expect("thumbnail_picture_id" in patchCharacter.mock.calls[0][1]).toBe(
      false,
    );
  });

  it("keeps the preview on its own control, not on the picture", async () => {
    const wrapper = await openEditor({ id: 99, name: "Alice" });
    await pickButtons(wrapper)[0].trigger("click");
    expect(document.querySelector(".ref-preview-overlay")).toBeNull();
    await wrapper.findAll(".ref-picture-zoom")[0].trigger("click");
    await flushPromises();
    expect(document.querySelector(".ref-preview-overlay")).not.toBeNull();
    wrapper.unmount();
  });
});
