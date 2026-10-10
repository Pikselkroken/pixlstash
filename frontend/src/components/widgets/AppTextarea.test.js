import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import AppTextarea from "./AppTextarea.vue";

describe("AppTextarea highlight", () => {
  it("marks the words the text names, on a hidden copy", () => {
    const wrapper = mount(AppTextarea, {
      props: { modelValue: "Mira on a beach", highlight: ["mira"] },
    });
    const mark = wrapper.find("mark");
    expect(mark.text()).toBe("Mira");
    expect(mark.element.parentElement.getAttribute("aria-hidden")).toBe("true");
    // The copy carries the whole text, so the mark sits where the word is.
    expect(mark.element.parentElement.textContent).toBe("Mira on a beach\n");
  });

  it("draws no copy while there is nothing to mark", async () => {
    const wrapper = mount(AppTextarea, {
      props: { modelValue: "on a beach", highlight: ["mira"] },
    });
    expect(wrapper.find("[aria-hidden]").exists()).toBe(false);
    await wrapper.setProps({ highlight: [] , modelValue: "mira" });
    expect(wrapper.find("mark").exists()).toBe(false);
  });
});
