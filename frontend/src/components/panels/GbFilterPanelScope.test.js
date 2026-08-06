// The three scope filters (Media, Faces, Stacks) are native selects rather than
// segmented controls. These tests pin the wiring that the swap put at risk, not
// the styling: that each select round-trips its store field, and in particular
// that the Faces filter's `null` ("Any") survives a native select, where option
// values are otherwise coerced to strings and "null" would never equal null.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import GbFilterPanel from "./GbFilterPanel.vue";
import { useFilterStore } from "../../stores/useFilterStore.js";

vi.mock("../../api/tags", () => ({ listTags: vi.fn().mockResolvedValue([]) }));
vi.mock("../../api/comfyui", () => ({
  listComfyuiModels: vi.fn().mockResolvedValue([]),
  listComfyuiLoras: vi.fn().mockResolvedValue([]),
}));

function mountPanel() {
  return mount(GbFilterPanel, {
    props: { backendUrl: "", selectedCharacter: "ALL", allPicturesId: "ALL" },
    global: { stubs: { "v-icon": true } },
  });
}

// Find a scope select by the aria-label it exposes to a screen reader.
function scopeSelect(wrapper, ariaLabel) {
  const select = wrapper
    .findAll("select.gb-scope-select")
    .find((el) => el.attributes("aria-label") === ariaLabel);
  expect(select, `no select labelled "${ariaLabel}"`).toBeTruthy();
  return select;
}

describe("GbFilterPanel scope selects", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders the three scope filters as one labelled select each", () => {
    const wrapper = mountPanel();
    const labels = wrapper
      .findAll("select.gb-scope-select")
      .map((el) => el.attributes("aria-label"));
    expect(labels).toEqual([
      "Media type filter",
      "Face filter",
      "Stack state filter",
    ]);
  });

  it("offers three options per scope and no Unresolved stack state", () => {
    const wrapper = mountPanel();
    const stackLabels = scopeSelect(wrapper, "Stack state filter")
      .findAll("option")
      .map((el) => el.text());
    // 'unresolved' stays a valid store/API value; the duplicate queue owns it.
    expect(stackLabels).toEqual(["Any", "Stacked", "Unstacked"]);
  });

  it("writes the chosen media type and stack state to the store", async () => {
    const wrapper = mountPanel();
    const store = useFilterStore();

    await scopeSelect(wrapper, "Media type filter").setValue("videos");
    expect(store.mediaTypeFilter).toBe("videos");

    await scopeSelect(wrapper, "Stack state filter").setValue("unstacked");
    expect(store.stackStateFilter).toBe("unstacked");
  });

  it("round-trips the Faces filter's null through the native select", async () => {
    const wrapper = mountPanel();
    const store = useFilterStore();
    const faces = scopeSelect(wrapper, "Face filter");

    await faces.setValue("with_face");
    expect(store.faceBboxFilter).toBe("with_face");

    // The failure this guards: a native <option> value is a string, so "Any"
    // would come back as the string "null" and never clear the filter.
    const anyOption = faces.findAll("option")[0];
    await faces.setValue(anyOption.element.value);
    expect(store.faceBboxFilter).toBeNull();
  });

  it("marks a scope as active only when it is off its default", async () => {
    const wrapper = mountPanel();
    const stacks = scopeSelect(wrapper, "Stack state filter");
    expect(stacks.classes()).not.toContain("gb-scope-select--on");

    await stacks.setValue("stacked");
    expect(stacks.classes()).toContain("gb-scope-select--on");
  });
});
