// The set card wears the owner's verdict and nothing of PixlStash's check.
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
vi.mock("../../api/modelIcons", () => ({
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelSetCard from "./ModelSetCard.vue";

const card = (extra = {}) => ({
  key: "model:1",
  name: "Base",
  kindLabel: "Checkpoint",
  kinds: [],
  facts: ["2 models"],
  covers: [],
  pictures: 0,
  recipes: 0,
  ...extra,
});

describe("ModelSetCard verdict", () => {
  it("draws the owner's verdict icon", () => {
    const w = mount(ModelSetCard, {
      props: { card: card({ verdict: "no" }) },
    });
    const icon = w.get('[data-testid="verdict-icon"]');
    expect(icon.attributes("aria-label")).toBe(
      "Your verdict: this set does not produce sensible output",
    );
    expect(w.html()).toContain("mdi-close-circle");
  });

  it("draws nothing without a verdict, whatever the check says", () => {
    const w = mount(ModelSetCard, {
      props: { card: card({ verdict: null, check: { check: "pass" } }) },
    });
    expect(w.find('[data-testid="verdict-icon"]').exists()).toBe(false);
  });
});
