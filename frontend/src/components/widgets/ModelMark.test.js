// The shelf's identity slot.
//
// The assertion worth having is that it is NEVER blank: a checkpoint has no
// sample by construction, and 37% of real adapters carry no title, so the
// generated mark is the common path rather than the fallback.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("../../api/modelIcons", () => ({
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelMark from "./ModelMark.vue";

function row(overrides = {}) {
  return {
    display_name: "Cyanwood Style",
    filename: "Cyanwood_Style_000000250.safetensors",
    base_model: "flux.1-dev",
    icon_sha256: null,
    ...overrides,
  };
}

const mountMark = (props) => mount(ModelMark, { props: { row: row(props) } });

describe("ModelMark", () => {
  it("draws the icon when there is one", () => {
    const wrapper = mountMark({ icon_sha256: "a".repeat(64) });
    expect(wrapper.find("img").attributes("src")).toContain("a".repeat(64));
    expect(wrapper.find(".mmark-initials").exists()).toBe(false);
  });

  it("is never blank without one", () => {
    const wrapper = mountMark();
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find(".mmark-initials").text()).toBe("CS");
  });

  it("still marks a row with no name at all", () => {
    // `000002750.safetensors` strips to nothing, so the name chain falls back
    // to the raw filename — the mark has to survive that too.
    const wrapper = mountMark({
      display_name: null,
      filename: "000002750.safetensors",
    });
    expect(wrapper.find(".mmark-initials").text()).not.toBe("");
  });

  it("gives every spelling of one base model the same colour", () => {
    // The whole reason the mark keys on the FOLDED value: four spellings of
    // FLUX.2 scattering across the palette would defeat the point of a mark.
    const a = mountMark({ base_model: "FLUX.2", base_model_folded: "flux.2" });
    const b = mountMark({ base_model: "flux 2", base_model_folded: "flux.2" });
    expect(a.find(".mmark-initials").attributes("style")).toBe(
      b.find(".mmark-initials").attributes("style"),
    );
  });

  it("is stable for one row and does not depend on its neighbours", () => {
    // `character_color` takes the first UNUSED colour, which needs a bounded
    // set and a moment of assignment. A model's mark is a pure function of the
    // row, so deleting a neighbour cannot change it.
    const first = mountMark().find(".mmark-initials").attributes("style");
    const again = mountMark().find(".mmark-initials").attributes("style");
    expect(first).toBe(again);
  });

  it("does not announce itself, because the row already says the name", () => {
    // Found by class rather than through the wrapper root: `attributes()` on
    // the wrapper read undefined here, so a bare `.toContain("aria-hidden")`
    // would have been a substring match on the whole subtree rather than a
    // statement about this element.
    expect(mountMark().find(".mmark").attributes("aria-hidden")).toBe("true");
  });
});
