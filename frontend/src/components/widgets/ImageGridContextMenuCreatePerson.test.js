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
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false), // real ref so the menu template unwraps it
    onSessionReset: () => () => {},
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
