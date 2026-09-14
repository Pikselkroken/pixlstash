// AppInput - one field height, the label above, mono for paths and ids
// (docs/design/buttons.md, "Fields").

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import AppInput from "./AppInput.vue";

describe("AppInput", () => {
  it("puts the label above the box, never inside it", () => {
    const w = mount(AppInput, { props: { label: "Folder path" } });
    const label = w.find(".field-label");
    expect(label.text()).toBe("Folder path");
    expect(w.find(".app-input__wrap").find(".field-label").exists()).toBe(false);
    expect(label.element.nextElementSibling).toBe(
      w.find(".app-input__wrap").element,
    );
  });

  it("takes the mono face only when asked", async () => {
    const w = mount(AppInput);
    expect(w.find(".app-input__wrap--mono").exists()).toBe(false);
    await w.setProps({ mono: true });
    expect(w.find(".app-input__wrap--mono").exists()).toBe(true);
  });

  it("marks an error, and renders a string error as its message", async () => {
    const w = mount(AppInput, { props: { error: true } });
    expect(w.find("input").attributes("aria-invalid")).toBe("true");
    expect(w.find(".app-input__error").exists()).toBe(false);
    await w.setProps({ error: "Pick a date in the future" });
    expect(w.find(".app-input__error").text()).toBe("Pick a date in the future");
    await w.setProps({ error: false });
    expect(w.find("input").attributes("aria-invalid")).toBeUndefined();
  });

  it("passes readonly, the accessible name and the value through", async () => {
    const w = mount(AppInput, {
      props: { readonly: true, ariaLabel: "Destination", modelValue: "x" },
    });
    const input = w.find("input");
    expect(input.attributes("readonly")).toBeDefined();
    expect(input.attributes("aria-label")).toBe("Destination");
    await input.setValue("y");
    expect(w.emitted("update:modelValue")).toEqual([["y"]]);
  });

  it("keeps a min or max of 0", () => {
    const w = mount(AppInput, { props: { type: "number", min: 0, max: 0 } });
    expect(w.find("input").attributes("min")).toBe("0");
    expect(w.find("input").attributes("max")).toBe("0");
    expect(mount(AppInput).find("input").attributes("min")).toBeUndefined();
  });

  it("focuses itself on mount when autofocus is set", () => {
    const w = mount(AppInput, {
      props: { autofocus: true },
      attachTo: document.body,
    });
    expect(document.activeElement).toBe(w.find("input").element);
    w.unmount();
  });
});
