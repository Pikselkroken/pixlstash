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
    expect(w.find(".field-label").text()).toBe("Folder path");
    expect(w.find(".app-input__wrap").text()).toBe("");
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

  it("focuses itself on mount when autofocus is set", () => {
    const w = mount(AppInput, {
      props: { autofocus: true },
      attachTo: document.body,
    });
    expect(document.activeElement).toBe(w.find("input").element);
    w.unmount();
  });
});
