// Segmented and OptionRows - the two pick-one shapes (docs/design/buttons.md).

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import Segmented from "./Segmented.vue";
import OptionRows from "./OptionRows.vue";

const OPTIONS = [
  { id: "a", label: "Alpha", icon: "alpha" },
  { id: "b", label: "Beta", icon: "beta", disabled: true },
  { id: "c", label: "Gamma", icon: "gamma" },
];

describe.each([
  ["Segmented", Segmented],
  ["OptionRows", OptionRows],
])("%s radiogroup contract", (_, Component) => {
  it("marks exactly the selected option checked and gives it the one tab stop", () => {
    const w = mount(Component, {
      props: { options: OPTIONS, modelValue: "c", ariaLabel: "Pick" },
    });
    expect(w.attributes("role")).toBe("radiogroup");
    expect(w.attributes("aria-label")).toBe("Pick");
    const radios = w.findAll('[role="radio"]');
    expect(radios.map((r) => r.attributes("aria-checked"))).toEqual([
      "false",
      "false",
      "true",
    ]);
    expect(radios.map((r) => r.attributes("tabindex"))).toEqual([
      "-1",
      "-1",
      "0",
    ]);
  });

  it("puts the tab stop on the first live option when nothing is selected", () => {
    const w = mount(Component, {
      props: { options: [OPTIONS[1], OPTIONS[2]], modelValue: null },
    });
    const radios = w.findAll('[role="radio"]');
    expect(radios.map((r) => r.attributes("tabindex"))).toEqual(["-1", "0"]);
  });

  it("selects on click, and not again for the current value", async () => {
    const w = mount(Component, { props: { options: OPTIONS, modelValue: "a" } });
    const radios = w.findAll('[role="radio"]');
    await radios[0].trigger("click");
    expect(w.emitted("update:modelValue")).toBeUndefined();
    await radios[2].trigger("click");
    expect(w.emitted("update:modelValue")).toEqual([["c"]]);
  });

  it("arrows step in their own direction, skip disabled options, wrap, and keep the press", async () => {
    const options = [
      { id: "a", label: "Alpha" },
      { id: "b", label: "Beta", disabled: true },
      { id: "c", label: "Gamma" },
      { id: "d", label: "Delta" },
    ];
    const outer = vi.fn();
    document.body.addEventListener("keydown", outer);
    const w = mount(Component, {
      props: { options, modelValue: "a" },
      attachTo: document.body,
    });
    const radios = w.findAll('[role="radio"]');
    await w.trigger("keydown", { key: "ArrowRight" });
    expect(w.emitted("update:modelValue").at(-1)).toEqual(["c"]);
    expect(document.activeElement).toBe(radios[2].element);
    await w.trigger("keydown", { key: "ArrowLeft" });
    expect(w.emitted("update:modelValue").at(-1)).toEqual(["d"]);
    expect(document.activeElement).toBe(radios[3].element);
    await w.setProps({ modelValue: "d" });
    await w.trigger("keydown", { key: "ArrowDown" });
    expect(w.emitted("update:modelValue").at(-1)).toEqual(["a"]);
    await w.setProps({ modelValue: "c" });
    await w.trigger("keydown", { key: "ArrowUp" });
    expect(w.emitted("update:modelValue").at(-1)).toEqual(["a"]);
    // A handled arrow never reaches a window-level key model.
    expect(outer).not.toHaveBeenCalled();
    await w.trigger("keydown", { key: "Enter" });
    expect(w.emitted("update:modelValue")).toHaveLength(4);
    expect(outer).toHaveBeenCalledTimes(1);
    document.body.removeEventListener("keydown", outer);
    w.unmount();
  });

  it("starts from the ends when no value is selected yet", async () => {
    const w = mount(Component, { props: { options: OPTIONS, modelValue: "zz" } });
    await w.trigger("keydown", { key: "ArrowRight" });
    await w.trigger("keydown", { key: "ArrowLeft" });
    expect(w.emitted("update:modelValue")).toEqual([["a"], ["c"]]);
  });

  it("does nothing while the whole group is disabled", async () => {
    const w = mount(Component, {
      props: { options: OPTIONS, modelValue: "a", disabled: true },
    });
    await w.findAll('[role="radio"]')[2].trigger("click");
    await w.trigger("keydown", { key: "ArrowRight" });
    expect(w.emitted("update:modelValue")).toBeUndefined();
  });
});

describe("Segmented", () => {
  it("fills the selected segment and draws icons only where the variant asks", () => {
    const labelOnly = mount(Segmented, {
      props: { options: OPTIONS, modelValue: "a" },
    });
    expect(labelOnly.find(".seg__opt--on").text()).toBe("Alpha");
    expect(labelOnly.find("i").exists()).toBe(false);

    const iconOnly = mount(Segmented, {
      props: {
        options: [{ id: "g", icon: "view-grid", title: "Grid" }],
        variant: "icon",
      },
    });
    const radio = iconOnly.find('[role="radio"]');
    expect(radio.attributes("aria-label")).toBe("Grid");
    expect(radio.find("i").text()).toBe("mdi-view-grid");
    expect(radio.find(".seg__label").exists()).toBe(false);
  });

  it("picks on a click of the current value, so a snap stop can re-snap", async () => {
    const w = mount(Segmented, { props: { options: OPTIONS, modelValue: "a" } });
    await w.findAll('[role="radio"]')[0].trigger("click");
    expect(w.emitted("pick")).toEqual([["a"]]);
    expect(w.emitted("update:modelValue")).toBeUndefined();
  });
});

describe("OptionRows", () => {
  it("takes no fill: every row carries an aligned radio indicator", () => {
    const w = mount(OptionRows, {
      props: { options: OPTIONS, modelValue: "c" },
    });
    const rows = w.findAll('[role="radio"]');
    expect(w.findAll(".optrow__radio")).toHaveLength(3);
    expect(
      rows.map((row) => row.find(".optrow__radio").text().trim()),
    ).toEqual(["mdi-radiobox-blank", "mdi-radiobox-blank", "mdi-radiobox-marked"]);
  });

  it("draws the radio as each row's only glyph, even for an option with an icon", () => {
    const w = mount(OptionRows, {
      props: { options: OPTIONS, modelValue: "c" },
    });
    for (const row of w.findAll('[role="radio"]')) {
      expect(row.findAll("i").map((i) => i.text().trim())).toEqual([
        expect.stringMatching(/^mdi-radiobox-/),
      ]);
    }
  });

  it("picks on a click, even of the current value, and never on an arrow", async () => {
    const w = mount(OptionRows, {
      props: { options: OPTIONS, modelValue: "a" },
      attachTo: document.body,
    });
    await w.trigger("keydown", { key: "ArrowRight" });
    expect(w.emitted("update:modelValue")).toEqual([["c"]]);
    expect(w.emitted("pick")).toBeUndefined();
    const radios = w.findAll('[role="radio"]');
    await radios[0].trigger("click");
    await radios[2].trigger("click");
    expect(w.emitted("pick")).toEqual([["a"], ["c"]]);
    w.unmount();
  });
});
