// NewReviewDialog - the Set-scope custom listbox and its locked-set handling.
//
// The set scope is a custom listbox (not a native <select>) so a locked set can
// render greyed, with a lock icon, and be non-selectable - a <select>'s
// <option>s can't do that. These tests drive that behaviour: a locked set row
// is present, marked disabled, and clicking it does NOT change the selection;
// an unlocked set row selects normally.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { nextTick, h } from "vue";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: { value: false },
}));

import NewReviewDialog from "./NewReviewDialog.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useEntityListsStore } from "../../stores/useEntityListsStore";

const VIcon = {
  name: "v-icon",
  setup:
    (_props, { slots }) =>
    () =>
      h("i", { class: "v-icon" }, slots.default?.()),
};

const globalOpts = { stubs: { "v-icon": VIcon } };

function seedStore() {
  const store = useReviewSessionsStore();
  store.healthRows = [];
  // The scope lists are a view onto the shared entity-list cache, so they are
  // seeded there. One unlocked set and one locked set - `locked` arrives free
  // from the API (PictureSetResponse.locked via safe_model_dict), so the dialog
  // reads it off store.sets directly.
  useEntityListsStore().lists = {
    characters: [],
    projects: [],
    sets: [
      { id: 1, name: "Portraits", locked: false },
      { id: 2, name: "Frozen eval", locked: true },
    ],
  };
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
    expect(locked.attributes("title")).toContain(
      "Unlock it to review its tags",
    );
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

  // Standard listbox contract: arrow-key traversal VISITS disabled rows so a
  // keyboard-only user can reach the locked row and hear its aria-disabled
  // state and lock explanation. Only activation stays blocked.
  it("lets arrow-key traversal land on a locked row", async () => {
    seedStore();
    const w = mount(NewReviewDialog, { global: globalOpts });
    await openSetMenu(w);

    const menu = w.find(".rs-listbox-menu");
    // Opens on "Any" (index 0). Down → Portraits (1) → Frozen eval (2, locked).
    await menu.trigger("keydown", { key: "ArrowDown" });
    await menu.trigger("keydown", { key: "ArrowDown" });
    await nextTick();

    expect(menu.attributes("aria-activedescendant")).toBe("rs-set-opt-2");
    const active = w.find(".rs-listbox-option--active");
    expect(active.classes()).toContain("rs-listbox-option--locked");
    expect(active.attributes("aria-disabled")).toBe("true");
  });

  it("End reaches the locked last row, and Enter there does not select it", async () => {
    seedStore();
    const w = mount(NewReviewDialog, { global: globalOpts });
    await openSetMenu(w);

    const menu = w.find(".rs-listbox-menu");
    await menu.trigger("keydown", { key: "End" });
    await nextTick();
    expect(menu.attributes("aria-activedescendant")).toBe("rs-set-opt-2");

    await menu.trigger("keydown", { key: "Enter" });
    await nextTick();
    // Activation is still blocked: selection unchanged, menu still open.
    expect(w.find(".rs-listbox-value").text()).toBe("Any");
    expect(w.find(".rs-listbox-menu").exists()).toBe(true);
  });

  it("arrow traversal still selects an unlocked row on Enter", async () => {
    seedStore();
    const w = mount(NewReviewDialog, { global: globalOpts });
    await openSetMenu(w);

    const menu = w.find(".rs-listbox-menu");
    await menu.trigger("keydown", { key: "ArrowDown" }); // Portraits
    await menu.trigger("keydown", { key: "Enter" });
    await nextTick();

    expect(w.find(".rs-listbox-value").text()).toBe("Portraits");
    expect(w.find(".rs-listbox-menu").exists()).toBe(false);
  });
});

// A locked setId can be prefilled straight from the launch context
// (ReviewSessionsOverlay passes store.healthScope through as `initialScope`),
// bypassing selectSet()'s click-time guard - so the trigger itself has to show
// the lock rather than letting the user discover the block on submit.
describe("NewReviewDialog locked prefilled set scope", () => {
  it("marks the trigger as locked when initialScope prefills a locked set", () => {
    seedStore();
    const w = mount(NewReviewDialog, {
      props: { initialScope: { projectId: null, setId: 2, characterId: null } },
      global: globalOpts,
    });

    const trigger = w.find(".rs-listbox-trigger");
    expect(w.find(".rs-listbox-value").text()).toBe("Frozen eval");
    expect(trigger.classes()).toContain("rs-listbox-trigger--locked");
    expect(trigger.find(".rs-listbox-trigger-lock").exists()).toBe(true);
    expect(trigger.attributes("title")).toContain("is locked");
    expect(trigger.attributes("title")).toContain(
      "Unlock it to review its tags",
    );
  });

  it("blocks Scan & create while a locked set is the prefilled scope", () => {
    seedStore();
    const w = mount(NewReviewDialog, {
      props: {
        preset: "shirt",
        initialScope: { projectId: null, setId: 2, characterId: null },
      },
      global: globalOpts,
    });

    const go = w.find(".rs-dialog-btn--go");
    expect(go.attributes("disabled")).toBeDefined();
    expect(go.attributes("title")).toContain("Unlock it to review its tags");
  });

  it("allows Scan & create for an unlocked prefilled scope", () => {
    seedStore();
    const w = mount(NewReviewDialog, {
      props: {
        preset: "shirt",
        initialScope: { projectId: null, setId: 1, characterId: null },
      },
      global: globalOpts,
    });

    const go = w.find(".rs-dialog-btn--go");
    expect(go.attributes("disabled")).toBeUndefined();
    expect(go.attributes("title")).toBeUndefined();
  });

  it("leaves the trigger unmarked for an unlocked prefilled set", () => {
    seedStore();
    const w = mount(NewReviewDialog, {
      props: { initialScope: { projectId: null, setId: 1, characterId: null } },
      global: globalOpts,
    });

    const trigger = w.find(".rs-listbox-trigger");
    expect(w.find(".rs-listbox-value").text()).toBe("Portraits");
    expect(trigger.classes()).not.toContain("rs-listbox-trigger--locked");
    expect(trigger.find(".rs-listbox-trigger-lock").exists()).toBe(false);
    expect(trigger.attributes("title")).toBe("Portraits");
  });

  it("opens the menu with the locked prefilled row active", async () => {
    seedStore();
    const w = mount(NewReviewDialog, {
      props: { initialScope: { projectId: null, setId: 2, characterId: null } },
      global: globalOpts,
    });
    await openSetMenu(w);

    expect(w.find(".rs-listbox-menu").attributes("aria-activedescendant")).toBe(
      "rs-set-opt-2",
    );
  });
});
