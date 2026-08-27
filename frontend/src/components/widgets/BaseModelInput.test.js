// The base-model field's completion behaviour.
//
// The assertions worth having are the ones the feature would be wrong without:
// the list is offered but never imposed (the column is free text, so a string
// nobody has heard of must survive being typed), matching ignores spelling so
// `sdxl` finds `SDXL 1.0`, and Enter takes the highlight rather than the raw
// text - the last is the one that would still pass with the logic inverted if
// the test only checked that Enter commits.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listBaseModelCompletions = vi.fn();
vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: vi.fn().mockResolvedValue([]),
  listCheckpoints: vi.fn().mockResolvedValue([]),
  listBaseModelCompletions: (...args) => listBaseModelCompletions(...args),
}));

import BaseModelInput from "./BaseModelInput.vue";

// Every mount is remembered and unmounted after the test. The menu teleports
// to `<body>` and the component listens on `window`, so a wrapper left mounted
// keeps reacting to the next test's events with its DOM already cleared.
const mounted = [];

function make(props) {
  const wrapper = mount(BaseModelInput, props);
  mounted.push(wrapper);
  return wrapper;
}

/** Mount with `v-model` really bound and the list already fetched. */
async function field(value = "") {
  const wrapper = make({
    props: {
      modelValue: value,
      "onUpdate:modelValue": (next) => wrapper.setProps({ modelValue: next }),
    },
    attachTo: document.body,
  });
  // ArrowDown is the gesture that opens the menu, and the fetch it kicks off
  // has to land before anything can be offered.
  await wrapper.find("input").trigger("keydown", { key: "ArrowDown" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  await wrapper.vm.$nextTick();
  return wrapper;
}

function menuItems() {
  return [...document.querySelectorAll(".bmi-item")].map((el) =>
    el.textContent.replace(/\s+/g, " ").trim(),
  );
}

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

beforeEach(() => {
  setActivePinia(createPinia());
  document.body.innerHTML = "";
  listBaseModelCompletions.mockReset();
  listBaseModelCompletions.mockResolvedValue([
    "FLUX.2",
    "SDXL 1.0",
    "Clementine ZIB 3B",
    // Holds `flux` in the middle rather than at the front: the only input that
    // tells a prefix match apart from a substring one.
    "Pony FLUX Mix",
  ]);
});

describe("the base-model completion list", () => {
  it("matches on the folded spelling, not the literal one", async () => {
    // `sdxl` is how the field is typed and `SDXL 1.0` is how it is stored; a
    // literal prefix match would offer nothing for the commonest input there is.
    const wrapper = await field();
    await wrapper.find("input").setValue("sdxl");
    await wrapper.vm.$nextTick();

    expect(menuItems().map((t) => t.replace(" TAB", ""))).toEqual(["SDXL 1.0"]);
  });

  it("commits a string the list has never heard of, unaltered", async () => {
    // The column is free text by rule. Completion offers; it must not constrain,
    // or every base model released after this build becomes untypeable. The
    // assertion is on the COMMIT rather than on the echo: an Enter that snapped
    // to the nearest known label would pass any test that only watched typing.
    const wrapper = await field();
    const input = wrapper.find("input");
    await input.setValue("Nobody Has This 9");
    await wrapper.vm.$nextTick();
    expect(menuItems()).toEqual([]);

    await input.trigger("keydown", { key: "Enter" });
    await wrapper.vm.$nextTick();

    expect(wrapper.emitted("confirm")).toHaveLength(1);
    expect(wrapper.props("modelValue")).toBe("Nobody Has This 9");
  });

  it("puts prefix matches ahead of the ones that merely contain it", async () => {
    // Typing has to narrow predictably: `flux` means the FLUX base far more
    // often than it means somebody's mix, so a list that reshuffled or ordered
    // the other way would make Tab pick the wrong one.
    const wrapper = await field();
    await wrapper.find("input").setValue("flux");
    await wrapper.vm.$nextTick();

    expect(menuItems().map((t) => t.replace(" TAB", ""))).toEqual([
      "FLUX.2",
      "Pony FLUX Mix",
    ]);
  });

  it("offers no menu once the field says exactly the one thing left", async () => {
    // There is nothing to complete, and the menu would only cover the row under
    // a field that is already finished.
    const wrapper = await field();
    await wrapper.find("input").setValue("SDXL 1.0");
    await wrapper.vm.$nextTick();

    expect(menuItems()).toEqual([]);
  });

  it("stays shut until a key asks for it", async () => {
    // Both hosts focus this field as they draw it. A menu that opened with them
    // would cover the dialog unasked and would eat the Escape that dismisses it.
    const wrapper = make({
      props: { modelValue: "", "onUpdate:modelValue": () => {} },
      attachTo: document.body,
    });
    await wrapper.find("input").trigger("focus");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(menuItems()).toEqual([]);
    expect(wrapper.find("input").attributes("aria-expanded")).toBe("false");
  });

  it("names the menu and its highlight for a screen reader", async () => {
    // Without these the field announces "expanded" and gives no way to reach
    // what expanded, and the Arrow highlight is never read out at all.
    const wrapper = await field();
    const input = wrapper.find("input");
    const menuId = document.querySelector(".bmi-menu").id;
    expect(menuId).toBeTruthy();
    expect(input.attributes("aria-controls")).toBe(menuId);
    expect(input.attributes("aria-activedescendant")).toBeUndefined();

    await input.trigger("keydown", { key: "ArrowDown" });
    const active = input.attributes("aria-activedescendant");
    expect(active).toBeTruthy();
    expect(document.getElementById(active)?.textContent).toContain("FLUX.2");

    // And the pointer is not left aimed at an element that is gone.
    await input.trigger("keydown", { key: "Escape" });
    await wrapper.vm.$nextTick();
    expect(input.attributes("aria-controls")).toBeUndefined();
  });

  it("leaves Shift+Tab alone, so there is a way back out", async () => {
    const wrapper = await field();
    const input = wrapper.find("input");
    await input.setValue("fl");
    await wrapper.vm.$nextTick();

    await input.trigger("keydown", { key: "Tab", shiftKey: true });

    expect(wrapper.props("modelValue")).toBe("fl");
  });

  it("closes when the list underneath scrolls", async () => {
    // The menu is positioned once, `fixed`. Left open it would sit over
    // whichever rows scrolled into its place and still be clickable.
    const wrapper = await field();
    expect(menuItems().length).toBeGreaterThan(0);

    window.dispatchEvent(new Event("scroll"));
    await wrapper.vm.$nextTick();

    expect(menuItems()).toEqual([]);
  });

  it("commits what the highlight says, not what was typed", async () => {
    const wrapper = await field();
    const input = wrapper.find("input");
    await input.setValue("fl");
    await wrapper.vm.$nextTick();

    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "Enter" });
    await wrapper.vm.$nextTick();

    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual(["FLUX.2"]);
    expect(wrapper.emitted("confirm")).toHaveLength(1);
  });

  it("fills on Tab without committing", async () => {
    // Tab is the "I meant that one, but I am not done" key. Committing here
    // would turn a completion into a bulk write nobody asked for.
    const wrapper = await field();
    const input = wrapper.find("input");
    await input.setValue("clem");
    await wrapper.vm.$nextTick();

    await input.trigger("keydown", { key: "Tab" });

    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual([
      "Clementine ZIB 3B",
    ]);
    expect(wrapper.emitted("confirm")).toBeUndefined();
  });

  it("gives the first Escape to the menu and the second to the caller", async () => {
    // Otherwise one Escape aimed at a dropdown throws the whole edit away.
    const wrapper = await field();
    const input = wrapper.find("input");
    await input.setValue("s");
    await wrapper.vm.$nextTick();
    expect(menuItems().length).toBeGreaterThan(0);

    await input.trigger("keydown", { key: "Escape" });
    await wrapper.vm.$nextTick();
    expect(menuItems()).toEqual([]);
    expect(wrapper.emitted("cancel")).toBeUndefined();

    await input.trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("cancel")).toHaveLength(1);
  });
});
