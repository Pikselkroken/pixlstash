// The slot picker's ranking (#1578): a favourite model leads inside its own
// group and shows the passive heart; it never leaves the group. A checklist
// keeps the order its caller gave.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";

const listFavourites = vi.fn();
vi.mock("../../api/favourites", () => ({
  listFavourites: (...args) => listFavourites(...args),
  setFavourites: vi.fn(),
}));

import WorkflowSetChooser from "./WorkflowSetChooser.vue";

// Vuetify is not installed in the test app; the menu renders its content inline.
const VMenuStub = {
  name: "VMenu",
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue"],
  setup(props, { slots }) {
    return () => (props.modelValue ? slots.default?.() : null);
  },
};

const row = (id, name, sha256) => ({
  id,
  display_name: name,
  filename: `${name.toLowerCase()}.safetensors`,
  sha256,
});

const SECTION = {
  id: "checkpoint",
  label: "Used with this checkpoint",
  items: [
    { id: 1, row: row(1, "Plain", "p".repeat(64)), detail: "2 GB" },
    { id: 2, row: row(2, "Liked", "f".repeat(64)), detail: "1 GB" },
    { id: 3, row: row(3, "Other", "o".repeat(64)), detail: "3 GB" },
  ],
};

function mountChooser(sections = [SECTION]) {
  const wrapper = mount(WorkflowSetChooser, {
    props: { open: true, sections, pick: true, label: "Add model" },
    global: {
      plugins: [createPinia()],
      stubs: { VMenu: VMenuStub, VIcon: true, ModelMark: true, Segmented: true },
    },
    attachTo: document.body,
  });
  return wrapper;
}

const names = (wrapper) =>
  wrapper.findAll(".wsc__nm").map((entry) => entry.text());

describe("the slot picker's favourites (#1578)", () => {
  beforeEach(() => {
    listFavourites.mockReset().mockResolvedValue({
      model: ["f".repeat(64)],
      workflow: [],
      set: [],
    });
  });

  it("a favourite model leads its group, and draws the passive heart", async () => {
    const wrapper = mountChooser();
    await flushPromises();
    expect(names(wrapper)).toEqual(["Liked", "Plain", "Other"]);
    const hearts = wrapper.findAll(".fav-heart--passive");
    expect(hearts).toHaveLength(1);
    expect(hearts[0].element.closest('[role="option"]').textContent).toContain("Liked");
    wrapper.unmount();
  });

  it("with no favourite the order is the caller's, and no heart is drawn", async () => {
    listFavourites.mockResolvedValue({ model: [], workflow: [], set: [] });
    const wrapper = mountChooser();
    await flushPromises();
    expect(names(wrapper)).toEqual(["Plain", "Liked", "Other"]);
    expect(wrapper.findAll(".fav-heart--passive")).toHaveLength(0);
    wrapper.unmount();
  });

  it("a favourite in a lower group never climbs over the group above", async () => {
    const above = {
      id: "sets",
      label: "In your SDXL sets",
      items: [{ id: 1, row: row(1, "Plain", "p".repeat(64)), detail: "" }],
    };
    const below = {
      id: "all",
      label: "All other models",
      items: [{ id: 2, row: row(2, "Liked", "f".repeat(64)), detail: "" }],
    };
    const wrapper = mountChooser([above, below]);
    await flushPromises();
    expect(names(wrapper)).toEqual(["Plain", "Liked"]);
    wrapper.unmount();
  });

  it("a checklist keeps the order its caller gave", async () => {
    const wrapper = mountChooser([{ ...SECTION, items: SECTION.items.map((i) => ({ ...i, checked: true })) }]);
    wrapper.setProps({ pick: false });
    await flushPromises();
    expect(names(wrapper)).toEqual(["Plain", "Liked", "Other"]);
    wrapper.unmount();
  });
});
