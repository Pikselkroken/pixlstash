import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

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

import VerdictIcon from "./VerdictIcon.vue";

const mountIcon = (props) => mount(VerdictIcon, { props });

describe("VerdictIcon", () => {
  it("draws a check for yes and not_problem, a cross for no and problem", () => {
    for (const v of ["yes", "not_problem"]) {
      const w = mountIcon({ verdict: v, label: "x" });
      expect(w.html()).toContain("mdi-check-circle");
      expect(w.classes()).toContain("verdict-icon--positive");
    }
    for (const v of ["no", "problem"]) {
      const w = mountIcon({ verdict: v, label: "x" });
      expect(w.html()).toContain("mdi-close-circle");
      expect(w.classes()).toContain("verdict-icon--negative");
    }
  });

  it("is a named status image when display only", () => {
    const w = mountIcon({ verdict: "yes", label: "Your verdict: fine" });
    expect(w.attributes("role")).toBe("img");
    expect(w.attributes("aria-label")).toBe("Your verdict: fine");
  });

  it("is a named button when interactive, and its click does not reach a row", async () => {
    const row = vi.fn();
    const w = mount(
      {
        components: { VerdictIcon },
        template: `<div @click="row"><VerdictIcon verdict="no" label="Change it" interactive @click="$emit('hit')" /></div>`,
        methods: { row },
        emits: ["hit"],
      },
      { attachTo: document.body },
    );
    const button = w.get("button");
    expect(button.attributes("aria-label")).toBe("Change it");
    await button.trigger("click");
    expect(w.emitted("hit")).toHaveLength(1);
    expect(row).not.toHaveBeenCalled();
    w.unmount();
  });
});
