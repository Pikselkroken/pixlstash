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

  it("jumps to a typed name", async () => {
    const w = mountRich();
    await combo(w).trigger("keydown", { key: "ArrowDown" });
    await combo(w).trigger("keydown", { key: "z" });
    expect(active(w).attributes("aria-label")).toBe("Zebra");
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
