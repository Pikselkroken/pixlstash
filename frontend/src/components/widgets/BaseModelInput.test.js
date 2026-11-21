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

// An entry's (x) draws a glyph; this suite mounts without Vuetify.
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

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

// Photo look's rows: the full list on focus, a remembered entry forgotten by
// its (x) or by Delete on its highlight, and Clear all as the list's last row.
// What would be wrong: Delete eating a character of the text, or Tab writing
// the first preset over the field the owner is only passing through.
describe("a field that opens on focus, with removable entries", () => {
  const OPTIONS = [
    { value: "Phone snapshot", detail: "Taken on a phone.", section: "Presets" },
    { value: "Old Pentax", section: "Your entries", removable: true },
    { value: "Webcam", section: "Your entries", removable: true },
  ];

  async function lookField(value = "Phone snapshot") {
    const wrapper = make({
      props: {
        modelValue: value,
        options: OPTIONS,
        openOnFocus: true,
        footAction: "Clear all your entries",
        "onUpdate:modelValue": (next) => wrapper.setProps({ modelValue: next }),
      },
      attachTo: document.body,
    });
    await wrapper.find("input").trigger("focus");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    return wrapper;
  }

  it("shows everything on focus, though the field already holds a preset", async () => {
    await lookField();
    // The stubbed glyph's name is its text here; Vuetify draws it as a class.
    expect(menuItems().map((t) => t.replace(" mdi-close", ""))).toEqual([
      "Phone snapshot Taken on a phone.",
      "Old Pentax",
      "Webcam",
      "Clear all your entries",
    ]);
    const sections = [...document.querySelectorAll(".bmi-section")].map((el) =>
      el.textContent.trim(),
    );
    expect(sections).toEqual(["Presets", "Your entries"]);
  });

  it("an entry's (x) removes it without picking it", async () => {
    const wrapper = await lookField();
    const removes = document.querySelectorAll(".bmi-remove");
    // Only the entries carry one, hidden from assistive tech.
    expect(removes).toHaveLength(2);
    expect(removes[0].getAttribute("aria-hidden")).toBe("true");
    removes[1].dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    expect(wrapper.emitted("remove")).toEqual([["Webcam"]]);
    expect(wrapper.emitted("pick")).toBeUndefined();
  });

  it("Delete removes the highlighted entry, and only once an arrow highlighted it", async () => {
    const wrapper = await lookField();
    const input = wrapper.find("input");
    await input.trigger("keydown", { key: "Delete" });
    expect(wrapper.emitted("remove")).toBeUndefined();
    await input.trigger("keydown", { key: "ArrowDown" });
    // A preset is not removable: Delete edits the text as usual.
    await input.trigger("keydown", { key: "Delete" });
    expect(wrapper.emitted("remove")).toBeUndefined();
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "Delete" });
    expect(wrapper.emitted("remove")).toEqual([["Old Pentax"]]);
  });

  it("Clear all is the last row in the arrow order, and Enter on it clears", async () => {
    const wrapper = await lookField();
    const input = wrapper.find("input");
    for (let n = 0; n < 5; n += 1) {
      await input.trigger("keydown", { key: "ArrowDown" });
    }
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("foot-action")).toHaveLength(1);
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("Tab leaves the field as it is, and a click on an option picks it", async () => {
    const wrapper = await lookField();
    await wrapper.find("input").trigger("keydown", { key: "Tab" });
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    await wrapper.find("input").trigger("focus");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    document
      .querySelectorAll(".bmi-item")[1]
      .dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    expect(wrapper.emitted("pick")).toEqual([["Old Pentax"]]);
  });

  it("filters on the sentence as well as the name once something is typed", async () => {
    const wrapper = await lookField("");
    await wrapper.find("input").setValue("phone.");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(menuItems()).toEqual(["Phone snapshot Taken on a phone."]);
  });
});

// Typing is not only a filter: what the owner typed is offered first, as its
// own option, and Enter takes it. What would be wrong: Enter taking the first
// preset over the typed words, or a Use row offered for a preset's own name.
describe("a field that offers the typed text as its own option", () => {
  const OPTIONS = [
    { value: "Phone snapshot", detail: "Taken on a phone.", section: "Presets" },
    { value: "Old Pentax", section: "Your entries", removable: true },
  ];

  async function typed(text) {
    const wrapper = make({
      props: {
        modelValue: "",
        options: OPTIONS,
        openOnFocus: true,
        ownText: true,
        "onUpdate:modelValue": (next) => wrapper.setProps({ modelValue: next }),
      },
      attachTo: document.body,
    });
    const input = wrapper.find("input");
    await input.trigger("focus");
    await input.setValue(text);
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    return wrapper;
  }
  const active = (wrapper) =>
    document.getElementById(wrapper.find("input").attributes("aria-activedescendant"));

  it("puts Use \"...\" first and highlighted, named for the typed text", async () => {
    const wrapper = await typed("snap");
    const rows = [...document.querySelectorAll(".bmi-item")];
    expect(rows[0].getAttribute("aria-label")).toBe("Use your own: snap");
    expect(rows[0].getAttribute("role")).toBe("option");
    expect(rows[1].textContent).toContain("Phone snapshot");
    expect(active(wrapper)).toBe(rows[0]);
  });

  it("Enter takes the typed text, not the first preset", async () => {
    const wrapper = await typed("snap");
    await wrapper.find("input").trigger("keydown", { key: "Enter" });
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual(["snap"]);
    expect(wrapper.emitted("confirm")).toHaveLength(1);
  });

  it("typed text equal to an option highlights it and offers no Use row", async () => {
    const wrapper = await typed("phone SNAPSHOT");
    const rows = [...document.querySelectorAll(".bmi-item")];
    expect(rows.some((row) => row.classList.contains("bmi-use"))).toBe(false);
    expect(active(wrapper)).toBe(rows[0]);
    expect(rows[0].textContent).toContain("Phone snapshot");
    await wrapper.find("input").trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual(["Phone snapshot"]);
  });
});
