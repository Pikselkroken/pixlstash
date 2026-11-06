// KeyCap - one keyboard key (visual-language.md, "The key-hint badge").
//
// Symbol keys are drawn as icons, and v-icon is aria-hidden, so the key's name
// must ride as hidden text or a screen reader hears an empty key.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import KeyCap from "./KeyCap.vue";

const mountKey = (k) =>
  mount(KeyCap, { props: { k }, global: { stubs: { "v-icon": true } } });

describe("KeyCap", () => {
  it.each([
    ["Shift", "Shift"],
    ["⇧", "Shift"],
    ["⌘", "Command"],
    ["Enter", "Enter"],
    ["↵", "Enter"],
    ["⌫", "Backspace"],
  ])("draws %s as an icon named %s", (k, name) => {
    const w = mountKey(k);
    expect(w.find("v-icon-stub").exists()).toBe(true);
    expect(w.find(".visually-hidden").text()).toBe(name);
    expect(w.classes()).toContain("keycap--icon");
    // The symbol itself is never printed: it is the speck being replaced.
    if (k !== name) expect(w.text()).not.toContain(k);
  });

  it("writes any other key as its own text", () => {
    const w = mountKey("Tab");
    expect(w.element.tagName).toBe("KBD");
    expect(w.text()).toBe("Tab");
    expect(w.find("v-icon-stub").exists()).toBe(false);
    expect(w.classes()).not.toContain("keycap--icon");
  });
});
