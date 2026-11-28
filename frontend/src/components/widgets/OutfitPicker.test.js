// Add existing outfits to a person's wardrobe (#2819): every outfit not
// theirs, narrowed by whose it is and by name, several at once.

import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});
vi.mock("../../api/pictures", () => ({ pictureThumbnailUrl: (id) => `/thumb/${id}.webp` }));

import OutfitPicker from "./OutfitPicker.vue";

const PEOPLE = [
  { id: 5, name: "Mira" },
  { id: 6, name: "Juno" },
];
const OUTFITS = [
  { id: 20, name: "Mustard raincoat", character_id: 5, picture_ids: [21] },
  { id: 25, name: "Yellow wellies", character_id: null, picture_ids: [26] },
  { id: 27, name: "Red scarf", character_id: 6, picture_ids: [28] },
];

const mountPicker = (props = {}) =>
  mount(OutfitPicker, {
    props: { open: true, person: { id: 6, name: "Juno" }, outfits: OUTFITS, people: PEOPLE, ...props },
  });
const offered = (w) =>
  w.findAll(".pp-cell").map((c) => c.attributes("data-testid").replace("outfit-pick-", ""));

describe("the outfit picker", () => {
  it("offers every outfit not the person's own, saying whose each is", async () => {
    const w = mountPicker();
    expect(offered(w)).toEqual(["20", "25"]);
    expect(w.text()).toContain("Mira's · adds a copy");
    expect(w.text()).toContain("Not anyone's");
    const facets = w
      .findAll(".pp-facet")
      .map((f) => `${f.find(".pp-facet__label").text()} ${f.find(".pp-facet__count").text()}`);
    expect(facets).toEqual(["Everyone's 2", "Not anyone's 1", "Mira 1"]);
  });

  it("narrows by whose an outfit is and by its name", async () => {
    const w = mountPicker();
    await w.find('[data-testid="outfit-facet-nobody"]').trigger("click");
    expect(offered(w)).toEqual(["25"]);
    await w.find('[data-testid="outfit-facet-person-5"]').trigger("click");
    expect(offered(w)).toEqual(["20"]);
    await w.find('[data-testid="outfit-facet-all"]').trigger("click");
    await w.find('[data-testid="outfit-search"] input').setValue("well");
    expect(offered(w)).toEqual(["25"]);
  });

  it("adds the outfits ticked, or the one double-clicked alone", async () => {
    const w = mountPicker();
    expect(w.find('[data-testid="outfit-add"]').attributes("disabled")).toBeDefined();
    await w.find('[data-testid="outfit-pick-20"]').trigger("click");
    await w.find('[data-testid="outfit-pick-25"]').trigger("click");
    expect(w.findAll(".pp-tick")).toHaveLength(2);
    await w.find('[data-testid="outfit-add"]').trigger("click");
    expect(w.emitted("add")[0][0].map((o) => o.id)).toEqual([20, 25]);

    await w.find('[data-testid="outfit-pick-25"]').trigger("dblclick");
    expect(w.emitted("add")[1][0].map((o) => o.id)).toEqual([25]);
  });
});
