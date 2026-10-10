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

  it("draws no copy when there are no words to look for", () => {
    const wrapper = mount(AppTextarea, {
      props: { modelValue: "mira", highlight: [] },
    });
    expect(wrapper.find("[aria-hidden]").exists()).toBe(false);
    expect(wrapper.find("mark").exists()).toBe(false);
  });

  it("keeps the copy level with a field that is already scrolled", async () => {
    const wrapper = mount(AppTextarea, {
      props: { modelValue: "on a beach", highlight: ["mira"] },
    });
    wrapper.find("textarea").element.scrollTop = 40;
    // No scroll event: the text changing is what has to carry it over.
    await wrapper.setProps({ modelValue: "on a beach, mira" });
    expect(wrapper.find("[aria-hidden]").element.scrollTop).toBe(40);
  });
});
