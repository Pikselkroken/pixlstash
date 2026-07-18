// NewReviewDialog — the Set-scope custom listbox and its locked-set handling.
//
// The set scope is a custom listbox (not a native <select>) so a locked set can
// render greyed, with a lock icon, and be non-selectable — a <select>'s
// <option>s can't do that. These tests drive that behaviour: a locked set row
// is present, marked disabled, and clicking it does NOT change the selection;
// an unlocked set row selects normally.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { nextTick, h } from "vue";

vi.mock("../../utils/apiClient", () => ({
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: { value: false },
}));

import NewReviewDialog from "./NewReviewDialog.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

const VIcon = {
  name: "v-icon",
  setup: (_props, { slots }) => () => h("i", { class: "v-icon" }, slots.default?.()),
};

const globalOpts = { stubs: { "v-icon": VIcon } };

function seedStore() {
  const store = useReviewSessionsStore();
  store.healthRows = [];
  store.projects = [];
  store.characters = [];
  // One unlocked set and one locked set — `locked` arrives free from the API
  // (PictureSetResponse.locked via safe_model_dict), so the dialog reads it off
  // store.sets directly.
  store.sets = [
    { id: 1, name: "Portraits", locked: false },
    { id: 2, name: "Frozen eval", locked: true },
  ];
  return store;
}

async function openSetMenu(w) {
  await w.find(".rs-listbox-trigger").trigger("click");
  await nextTick();
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("NewReviewDialog set-scope listbox", () => {
  it("renders a locked set as a greyed, non-selectable, lock-marked row", async () => {
    seedStore();
    const w = mount(NewReviewDialog, { global: globalOpts });
    await openSetMenu(w);

    const options = w.findAll(".rs-listbox-option");
    // Any + Portraits + Frozen eval.
    expect(options).toHaveLength(3);

    const locked = w.find(".rs-listbox-option--locked");
    expect(locked.exists()).toBe(true);
    expect(locked.text()).toContain("Frozen eval");
    expect(locked.attributes("aria-disabled")).toBe("true");
    expect(locked.attributes("title")).toContain("is locked");
    expect(locked.attributes("title")).toContain("Unlock it to review its tags");
    // Lock glyph is present on the locked row only.
    expect(locked.find(".rs-listbox-lock").exists()).toBe(true);
    expect(w.findAll(".rs-listbox-lock")).toHaveLength(1);
  });

  it("does not select a locked set when its row is clicked", async () => {
    seedStore();
    const w = mount(NewReviewDialog, { global: globalOpts });
    await openSetMenu(w);

    await w.find(".rs-listbox-option--locked").trigger("click");
    await nextTick();

    // Selection is unchanged (still "Any") and the menu stays open.
    expect(w.find(".rs-listbox-value").text()).toBe("Any");
    expect(w.find(".rs-listbox-menu").exists()).toBe(true);
  });

  it("selects an unlocked set and closes the menu", async () => {
    seedStore();
    const w = mount(NewReviewDialog, { global: globalOpts });
    await openSetMenu(w);

    const portraits = w
      .findAll(".rs-listbox-option")
      .find((o) => o.text().includes("Portraits"));
    expect(portraits.classes()).not.toContain("rs-listbox-option--locked");
    await portraits.trigger("click");
    await nextTick();

    expect(w.find(".rs-listbox-value").text()).toBe("Portraits");
    // Menu closed after a valid selection.
    expect(w.find(".rs-listbox-menu").exists()).toBe(false);
  });
});
