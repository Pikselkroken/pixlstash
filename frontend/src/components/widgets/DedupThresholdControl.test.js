// The shared similarity-threshold slider.
//
// Two surfaces mount this control, so these tests pin the things that would
// otherwise drift between them: that it stays absent until the server's policy
// has actually landed, that the number reads as a whole percentage, that the
// server's bounds and the step reach the input, and that it reports a committed
// Number rather than a string on every drag frame.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupThresholdControl from "./DedupThresholdControl.vue";

/** A loaded policy: a value with the server's floor and ceiling around it. */
const LOADED = { threshold: 0.876, min: 0.7, max: 0.99 };

function mountControl(props = {}) {
  return mount(DedupThresholdControl, { props: { ...LOADED, ...props } });
}

describe("DedupThresholdControl", () => {
  it.each([
    ["threshold", { threshold: null }],
    ["min", { min: null }],
    ["max", { max: null }],
  ])("renders nothing while %s is missing", (_name, missing) => {
    // Before the policy loads there is no honest number to show; a slider with
    // guessed bounds would invite a change against the wrong scale.
    const wrapper = mountControl(missing);
    expect(wrapper.find(".dth").exists()).toBe(false);
    expect(wrapper.find(".dth-input").exists()).toBe(false);
  });

  it("reads the threshold as a rounded whole percentage", () => {
    // The two call sites must print one format; a raw 0.876 or an 87.6% here is
    // the drift this component exists to prevent.
    expect(mountControl().find(".dth-value").text()).toBe("88%");
  });

  it("passes the server's bounds and the step to the input", () => {
    // The floor is the server's policy, not a number this component owns.
    const input = mountControl().find(".dth-input");
    expect(input.attributes("min")).toBe("0.7");
    expect(input.attributes("max")).toBe("0.99");
    expect(input.attributes("step")).toBe("0.01");
    expect(input.attributes("type")).toBe("range");
  });

  it("labels the input", () => {
    // The label has to point at this instance's input, since a page can mount
    // more than one control.
    const wrapper = mountControl({ label: "Similar enough at" });
    const label = wrapper.find(".dth-label");
    expect(label.text()).toBe("Similar enough at");
    expect(label.attributes("for")).toBe(
      wrapper.find(".dth-input").attributes("id"),
    );
  });

  it("emits a committed Number on change", async () => {
    // `change`, not `input`: a drag must not fire a request per frame, and the
    // payload must be a number, since the DOM hands over a string.
    const wrapper = mountControl();
    const input = wrapper.find(".dth-input");
    input.element.value = "0.92";
    await input.trigger("change");
    expect(wrapper.emitted("change")).toHaveLength(1);
    expect(wrapper.emitted("change")[0]).toEqual([0.92]);
  });

  it("does not emit while the value is merely dragged", async () => {
    const wrapper = mountControl();
    const input = wrapper.find(".dth-input");
    input.element.value = "0.92";
    await input.trigger("input");
    expect(wrapper.emitted("change")).toBeUndefined();
  });

  it("honours disabled", () => {
    // The caller decides when the threshold applies to nothing; the control
    // still shows the number in force rather than disappearing.
    const wrapper = mountControl({ disabled: true });
    expect(wrapper.find(".dth-input").attributes("disabled")).toBeDefined();
    expect(wrapper.find(".dth-value").text()).toBe("88%");
  });

  it("is enabled by default", () => {
    expect(
      mountControl().find(".dth-input").attributes("disabled"),
    ).toBeUndefined();
  });
});
