// The context menu's forwarding of the Person flyout's "create" intent (#645):
// AddToEntityControl (type character) emits "create" → the menu closes itself
// first and only then, on nextTick, emits "create-character" upward with the
// query (the delegateWith pattern, so focus handling stays correct).

import { describe, it, expect, beforeEach, vi } from "vitest";
import { nextTick } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false), // real ref so the menu template unwraps it
    onSessionReset: () => () => {},
    // The menu reads `useEntityListsStore.canSeeProjects` to decide whether the
    // Project row renders; that getter derives from the session's scope. `null`
    // is an owner session, which is what these assertions assume.
    sessionContext: ref(null),
  };
});

import ImageGridContextMenu from "./ImageGridContextMenu.vue";
import AddToEntityControl from "./AddToEntityControl.vue";

const REQUIRED = {
  allPicturesId: "ALL",
  unassignedPicturesId: "UNASSIGNED",
  scrapheapPicturesId: "SCRAPHEAP",
  backendUrl: "http://x",
};

// Stub the flyouts: this test exercises the menu's forwarding, not the flyout
// internals (those are covered in AddToEntityControl.test.js). Teleport is
// stubbed so the menu renders inline where the wrapper can query it.
const globalStubs = {
  global: {
    stubs: { "v-icon": true, teleport: true, AddToEntityControl: true },
  },
};

beforeEach(() => {
  setActivePinia(createPinia());
});

function mountMenu() {
  return mount(ImageGridContextMenu, {
    props: {
      ...REQUIRED,
      visible: true,
      selectedImageIds: ["10", "11"],
      selectedCharacter: "ALL",
    },
    ...globalStubs,
  });
}

describe("create-character forwarding", () => {
  it("closes first, then emits create-character with the query", async () => {
    const wrapper = mountMenu();
    const personFlyout = wrapper
      .findAllComponents(AddToEntityControl)
      .find((c) => c.props("type") === "character");
    expect(personFlyout).toBeTruthy();

    personFlyout.vm.$emit("create", "Alice");

    // The delegate pattern: close now, the upward event only after nextTick.
    expect(wrapper.emitted("close")).toBeTruthy();
    expect(wrapper.emitted("create-character")).toBeUndefined();
    await nextTick();
    expect(wrapper.emitted("create-character")).toEqual([["Alice"]]);
  });

  it("declares the create-character emit", () => {
    const wrapper = mountMenu();
    expect(wrapper.vm.$options.emits).toContain("create-character");
  });
});

describe("set and person flyout toggles (#1262)", () => {
  it.each([
    ["character", "added", "add-to-character"],
    ["character", "removed", "remove-from-character"],
    ["set", "added", "added-to-set"],
  ])("forwards %s %s without closing the menu", (type, event, upward) => {
    const wrapper = mountMenu();
    const flyout = wrapper
      .findAllComponents(AddToEntityControl)
      .find((c) => c.props("type") === type);
    const payload = { pictureIds: ["10"] };

    flyout.vm.$emit(event, payload);

    expect(wrapper.emitted(upward)).toEqual([[payload]]);
    expect(wrapper.emitted("close")).toBeUndefined();
  });

  it("closes when the host changes the selection under the open menu", async () => {
    const wrapper = mountMenu();
    // The grid clears the selection after an add in the Unassigned view.
    await wrapper.setProps({ selectedImageIds: [] });
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("does not close when it opens onto a new selection", async () => {
    const wrapper = mountMenu();
    await wrapper.setProps({ visible: false });
    await wrapper.setProps({ visible: true, selectedImageIds: ["12"] });
    expect(wrapper.emitted("close")).toBeUndefined();
  });
});
