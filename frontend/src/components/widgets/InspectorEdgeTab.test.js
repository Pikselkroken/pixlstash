// The closed inspector's edge tab is a cue, not a fixture (visual-language
// "Closed inspector"): it shows on a nudge, goes again five seconds later, and
// never vanishes while the pointer or focus is on it.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import InspectorEdgeTab from "./InspectorEdgeTab.vue";

const mountTab = (props = {}) =>
  mount(InspectorEdgeTab, {
    props: { label: "a", nudge: 0, ...props },
    attachTo: document.body,
    global: { stubs: { VIcon: true } },
  });

describe("InspectorEdgeTab", () => {
  let wrapper;
  const tab = () => wrapper.get("button");
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    wrapper?.unmount();
    vi.useRealTimers();
  });

  it("is hidden until a nudge, then hides again after five seconds", async () => {
    wrapper = mountTab();
    expect(tab().isVisible()).toBe(false);

    await wrapper.setProps({ nudge: 1 });
    expect(tab().isVisible()).toBe(true);

    await vi.advanceTimersByTimeAsync(4900);
    expect(tab().isVisible()).toBe(true);
    await vi.advanceTimersByTimeAsync(200);
    expect(tab().isVisible()).toBe(false);
  });

  it("restarts the five seconds on each new nudge", async () => {
    wrapper = mountTab();
    await wrapper.setProps({ nudge: 1 });
    await vi.advanceTimersByTimeAsync(4000);
    await wrapper.setProps({ nudge: 2 });
    await vi.advanceTimersByTimeAsync(4000);
    expect(tab().isVisible()).toBe(true);
    await vi.advanceTimersByTimeAsync(1100);
    expect(tab().isVisible()).toBe(false);
  });

  it("stays while focused, and goes five seconds after focus leaves", async () => {
    wrapper = mountTab();
    await wrapper.setProps({ nudge: 1 });
    tab().element.focus();
    await vi.advanceTimersByTimeAsync(6000);
    expect(tab().isVisible()).toBe(true);

    tab().element.blur();
    await tab().trigger("blur");
    await vi.advanceTimersByTimeAsync(5100);
    expect(tab().isVisible()).toBe(false);
  });

  it("hides at once when the label empties, until the next nudge", async () => {
    wrapper = mountTab();
    await wrapper.setProps({ nudge: 1 });
    await wrapper.setProps({ label: "" });
    expect(tab().isVisible()).toBe(false);
    await wrapper.setProps({ label: "b" });
    expect(tab().isVisible()).toBe(false);
    await wrapper.setProps({ nudge: 2 });
    expect(tab().isVisible()).toBe(true);
  });
});
