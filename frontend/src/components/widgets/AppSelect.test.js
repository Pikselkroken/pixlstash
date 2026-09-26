// AppSelect's two-line listbox (#1525): an option with `chips` turns the field
// into a select-only combobox that can draw them. Plain options keep the
// native <select>.

import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import AppSelect from "./AppSelect.vue";

const GROUP = "This stack · 3 workflows";
const OPTIONS = [
  { value: "a", label: "Krea — x", name: "Krea", chips: ["Cover"], group: GROUP },
  { value: "b", label: "Krea — + upscale", name: "Krea", chips: ["+ upscale", "flux"], group: GROUP },
  { value: "c", label: "Portrait", name: "Portrait", chips: [], group: GROUP },
  { value: "d", label: "Zebra", group: "Other workflows" },
];

function mountRich(modelValue = "b") {
  return mount(AppSelect, {
    props: { modelValue, label: "Workflow", options: OPTIONS },
    attachTo: document.body,
  });
}

const combo = (w) => w.find("[role='combobox']");
const opts = (w) => w.findAll("[role='option']");
const active = (w) =>
  w.find(`#${combo(w).attributes("aria-activedescendant")}`);

describe("AppSelect with chip options", () => {
  it("keeps the native select when no option carries chips", () => {
    const w = mount(AppSelect, {
      props: { modelValue: "a", options: [{ value: "a", label: "A" }] },
    });
    expect(w.find("select").exists()).toBe(true);
    expect(w.find("[role='combobox']").exists()).toBe(false);
  });

  it("shows the chosen name and only its first chip while closed", () => {
    const w = mountRich();
    expect(w.find("select").exists()).toBe(false);
    expect(combo(w).find(".app-select__value").text()).toBe("Krea");
    const chips = combo(w).findAll(".chip-row > .chip-row__chip");
    expect(chips.map((c) => c.text())).toEqual(["+ upscale"]);
    expect(combo(w).attributes("aria-expanded")).toBe("false");
    expect(w.find("[role='listbox']").exists()).toBe(false);
  });

  it("speaks the closed value whole, every chip included", () => {
    const w = mountRich();
    // What is not aria-hidden is what a screen reader reads as the value.
    const spoken = [...combo(w).element.querySelectorAll("*")]
      .filter((el) => !el.closest("[aria-hidden='true']") && !el.children.length)
      .map((el) => el.textContent.trim())
      .filter(Boolean);
    expect(spoken).toEqual(["Krea, + upscale, flux"]);
    // Required on a combobox (ARIA 1.2), open or not.
    expect(combo(w).attributes("aria-controls")).toBeTruthy();
  });

  it("opens on the chosen row, with one heading per group", async () => {
    const w = mountRich();
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    expect(combo(w).attributes("aria-expanded")).toBe("true");
    expect(active(w).attributes("aria-selected")).toBe("true");
    expect(active(w).attributes("aria-label")).toBe("Krea, + upscale, flux");
    expect(w.findAll(".ctx-label").map((g) => g.text())).toEqual([
      GROUP,
      "Other workflows",
    ]);
    expect(opts(w)[0].attributes("aria-label")).toBe("Krea, Cover");
  });

  it("moves with the arrows and picks with Enter, which the dialog never sees", async () => {
    const w = mountRich();
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    expect(active(w).attributes("aria-label")).toBe("Portrait");
    const enter = new KeyboardEvent("keydown", { key: "Enter", cancelable: true });
    combo(w).element.dispatchEvent(enter);
    await w.vm.$nextTick();
    expect(enter.defaultPrevented).toBe(true);
    expect(w.emitted("update:modelValue")).toEqual([["c"]]);
    expect(combo(w).attributes("aria-expanded")).toBe("false");
    w.unmount();
  });

  it("closes on Escape without letting the dialog close", async () => {
    const w = mountRich();
    const outer = vi.fn();
    w.element.parentElement.addEventListener("keydown", (e) => {
      if (e.key === "Escape") outer();
    });
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "Escape" });
    expect(combo(w).attributes("aria-expanded")).toBe("false");
    expect(outer).not.toHaveBeenCalled();
    expect(w.emitted("update:modelValue")).toBeUndefined();
    w.unmount();
  });

  it("picks a clicked row, and re-picking the current one emits nothing", async () => {
    const w = mountRich();
    await combo(w).trigger("click");
    await opts(w)[1].trigger("click");
    expect(w.emitted("update:modelValue")).toBeUndefined();
    await combo(w).trigger("click");
    await opts(w)[3].trigger("click");
    expect(w.emitted("update:modelValue")).toEqual([["d"]]);
  });

  it("jumps to a typed name, a repeated letter walking the matches", async () => {
    const w = mountRich("a");
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "k" });
    // From the cover, the next "k" is the second Krea, not the cover again.
    expect(active(w).attributes("aria-label")).toBe("Krea, + upscale, flux");
  });

  it("reads a space inside a typed name as part of it", async () => {
    const opts = [
      { value: "p", label: "Pix", chips: ["x"] },
      { value: "q", label: "Pix b", chips: ["y"] },
    ];
    const w = mount(AppSelect, { props: { modelValue: "p", options: opts } });
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    for (const key of ["p", "i", "x", " ", "b"]) {
      await combo(w).trigger("keydown", { key });
    }
    expect(combo(w).attributes("aria-expanded")).toBe("true");
    expect(active(w).attributes("aria-label")).toBe("Pix b, y");
    expect(w.emitted("update:modelValue")).toBeUndefined();
  });

  it("takes the active row on Tab", async () => {
    const w = mountRich();
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "Tab" });
    expect(w.emitted("update:modelValue")).toEqual([["c"]]);
  });

  it("keeps the value when Shift+Tab backs out of the list", async () => {
    const w = mountRich();
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "Tab", shiftKey: true });
    expect(combo(w).attributes("aria-expanded")).toBe("false");
    expect(w.emitted("update:modelValue")).toBeUndefined();
  });

  it("names each run of rows as a group", async () => {
    const w = mountRich();
    await combo(w).trigger("click");
    const groups = w.findAll("[role='group']");
    expect(groups.map((g) => w.find(`#${g.attributes("aria-labelledby")}`).text()))
      .toEqual([GROUP, "Other workflows"]);
    expect(groups.map((g) => g.findAll("[role='option']").length)).toEqual([3, 1]);
  });

  it("closes and stops picking once disabled", async () => {
    const w = mountRich();
    await combo(w).trigger("click");
    const row = opts(w)[3];
    await w.setProps({ disabled: true });
    expect(w.find("[role='listbox']").exists()).toBe(false);
    await row.trigger("click");
    expect(w.emitted("update:modelValue")).toBeUndefined();
  });

  it("keeps focus on the field when the options gain or lose chips", async () => {
    const plain = [{ value: "a", label: "A" }];
    const w = mount(AppSelect, {
      props: { modelValue: "a", options: plain },
      attachTo: document.body,
    });
    w.find("select").element.focus();
    await w.setProps({ options: OPTIONS });
    await w.vm.$nextTick();
    expect(document.activeElement).toBe(combo(w).element);
    await w.setProps({ options: plain });
    await w.vm.$nextTick();
    expect(document.activeElement).toBe(w.find("select").element);
    w.unmount();
  });

  it("keeps the highlight on its row when the options change under it", async () => {
    const w = mountRich();
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "End" });
    expect(active(w).attributes("aria-label")).toBe("Zebra");
    // A row arrives ahead of it: the highlight moves with Zebra.
    await w.setProps({ options: [{ value: "n", label: "New", chips: ["x"] }, ...OPTIONS] });
    expect(active(w).attributes("aria-label")).toBe("Zebra");
    // Zebra goes: the highlight stays inside the list rather than past it.
    await w.setProps({ options: OPTIONS.slice(0, 2) });
    expect(active(w).attributes("aria-label")).toBe("Krea, + upscale, flux");
  });

  it("scrolls the list to the active row after the options change", async () => {
    const w = mountRich();
    await combo(w).trigger("click");
    // Reordered and shrunk under the open list, as a late card read can.
    await w.setProps({
      options: [
        { value: "n", label: "New", chips: ["x"] },
        OPTIONS[0],
        OPTIONS[1],
      ],
    });
    // jsdom has no layout: every row 40px tall, the list 50px high.
    const menu = w.find("[role='listbox']").element;
    Object.defineProperty(menu, "clientHeight", { value: 50 });
    opts(w).forEach((row, i) => {
      Object.defineProperty(row.element, "offsetTop", { value: i * 40 });
      Object.defineProperty(row.element, "offsetHeight", { value: 40 });
    });
    await combo(w).trigger("keydown", { key: "End" });
    await w.vm.$nextTick();
    // The last row (80-120px) is brought fully into the 50px window.
    expect(menu.scrollTop).toBe(70);
  });

  it("starts a fresh name each time the list opens", async () => {
    const w = mountRich("a");
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "k" });
    await combo(w).trigger("keydown", { key: "Escape" });
    // Straight after closing, Space opens the list; it is not more of "k".
    await combo(w).trigger("keydown", { key: " " });
    expect(combo(w).attributes("aria-expanded")).toBe("true");
  });

  it("does not open while disabled", async () => {
    const w = mount(AppSelect, {
      props: { modelValue: "a", options: OPTIONS, disabled: true },
    });
    await combo(w).trigger("click");
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    expect(combo(w).attributes("aria-expanded")).toBe("false");
  });
});
