import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("vuetify/components", async (importOriginal) => ({
  ...(await importOriginal()),
  VIcon: { name: "VIcon", template: "<i><slot /></i>" },
  VTooltip: {
    name: "VTooltip",
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

import VerdictAsk from "./VerdictAsk.vue";

const OPTIONS = [
  { value: "yes", label: "Yes", name: "Yes, it is fine" },
  { value: "no", label: "No", name: "No, it is not fine" },
];

function mountAsk(props = {}) {
  return mount(VerdictAsk, {
    props: { options: OPTIONS, answeredText: "it is fine", ...props },
    slots: { default: "Is it fine?" },
    attachTo: document.body,
  });
}

describe("VerdictAsk", () => {
  it("asks with two buttons carrying their full accessible names", () => {
    const w = mountAsk();
    expect(w.text()).toContain("Is it fine?");
    const names = w.findAll("button").map((b) => b.attributes("aria-label"));
    expect(names).toEqual(["Yes, it is fine", "No, it is not fine"]);
    expect(w.find("[aria-pressed]").exists()).toBe(false);
    w.unmount();
  });

  it("emits the answer", async () => {
    const w = mountAsk();
    await w.get('[data-testid="verdict-no"]').trigger("click");
    expect(w.emitted("answer")).toEqual([["no"]]);
    w.unmount();
  });

  it("collapses to one icon whose name states the verdict", () => {
    const w = mountAsk({ verdict: "yes" });
    expect(w.findAll("button")).toHaveLength(1);
    expect(w.get("button").attributes("aria-label")).toBe(
      "Your verdict: it is fine. Click to change.",
    );
    expect(w.text()).not.toContain("Is it fine?");
    w.unmount();
  });

  it("reopens with the answer pressed, focus on it, help text and Cancel", async () => {
    const w = mountAsk({ verdict: "no" });
    await w.get("button").trigger("click");
    await nextTick();
    const yes = w.get('[data-testid="verdict-yes"]');
    const no = w.get('[data-testid="verdict-no"]');
    expect(yes.attributes("aria-pressed")).toBe("false");
    expect(no.attributes("aria-pressed")).toBe("true");
    expect(document.activeElement).toBe(no.element);
    expect(w.text()).toContain(
      "This is a note for you; it changes nothing else.",
    );
    expect(w.find('[data-testid="verdict-cancel"]').exists()).toBe(true);
    w.unmount();
  });

  it("Cancel returns to the icon and focuses it", async () => {
    const w = mountAsk({ verdict: "no" });
    await w.get("button").trigger("click");
    await w.get('[data-testid="verdict-cancel"]').trigger("click");
    await nextTick();
    await nextTick();
    expect(w.findAll("button")).toHaveLength(1);
    expect(document.activeElement).toBe(w.get("button").element);
    expect(w.emitted("answer")).toBeUndefined();
    w.unmount();
  });

  it("focuses the icon once the answer arrives through the prop", async () => {
    const w = mountAsk();
    await w.get('[data-testid="verdict-yes"]').trigger("click");
    await w.setProps({ verdict: "yes" });
    await nextTick();
    await nextTick();
    expect(document.activeElement).toBe(w.get("button").element);
    w.unmount();
  });
});

describe("VerdictAsk refused save", () => {
  it("shows the error as an alert and reopens an answered question", async () => {
    const w = mountAsk({ verdict: "yes" });
    expect(w.find('[role="alert"]').exists()).toBe(false);
    await w.setProps({ error: "Could not save." });
    await nextTick();
    expect(w.get('[role="alert"]').text()).toContain("Could not save.");
    expect(w.findAll("button").length).toBeGreaterThan(1);
    w.unmount();
  });

  it("emits cancel", async () => {
    const w = mountAsk({ verdict: "yes" });
    await w.get("button").trigger("click");
    await w.get('[data-testid="verdict-cancel"]').trigger("click");
    expect(w.emitted("cancel")).toHaveLength(1);
    w.unmount();
  });
});
