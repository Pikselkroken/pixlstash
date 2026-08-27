// The shelf's identity slot.
//
// The assertion worth having is that it is NEVER blank: a checkpoint has no
// sample by construction, and 37% of real adapters carry no title, so the
// generated mark is the common path rather than the fallback.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, ref } from "vue";

// The api modules are deliberately NOT mocked. A mock would hardcode the
// prefixed string, which is exactly the shape that let the shelf ship with
// marks that drew nothing: every one of them agreed on a URL the real code did
// not produce. Here the real builders compute it.
import { API_BASE_URL } from "../../utils/apiClient";

import ModelMark from "./ModelMark.vue";

const ADA_FACE = `${API_BASE_URL}/characters/4/thumbnail`;
const icon = (sha) => `${API_BASE_URL}/model-icons/${sha}`;

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
    // The whole URL, not a substring: `toContain("model-icons")` passes just
    // as happily against a base with no `/api/v1` in it, which is the bug.
    expect(wrapper.find("img").attributes("src")).toBe(icon("a".repeat(64)));
    expect(wrapper.find(".mmark-initials").exists()).toBe(false);
  });

  it("is never blank without one", () => {
    const wrapper = mountMark();
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find(".mmark-initials").text()).toBe("CS");
  });

  it("still marks a row with no name at all", () => {
    // `000002750.safetensors` strips to nothing, so the name chain falls back
    // to the raw filename - the mark has to survive that too.
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

  it("does not announce the PICTURE, because the row already says the name", () => {
    // The face is hidden and the ring's label is not: the row's own name says
    // which model this is, but nothing else on it says what the model is
    // assigned to now that the column is gone (#904). Found by class rather
    // than through the wrapper root: `attributes()` on the wrapper read
    // undefined here, so a bare `.toContain("aria-hidden")` would have been a
    // substring match on the whole subtree rather than a statement about this
    // element.
    const mark = mountMark();
    expect(mark.find(".mmark-face").attributes("aria-hidden")).toBe("true");
    expect(mark.find(".mmark").attributes("aria-hidden")).toBeUndefined();
  });

  // The fallback chain. Each step is only reachable when the one above it has
  // actually failed, so the test drives the `error` event rather than asserting
  // on a flag: a missing icon FILE and a person with no reference face are both
  // ordinary states, and a mark that stopped at the first 404 would be the same
  // blank square the shelf was reported as showing.
  const RING = { type: "character", id: 4, style: "dashed", label: "Ada" };

  it("borrows the assigned person's face when the row has no icon", () => {
    const wrapper = mount(ModelMark, { props: { row: row(), ring: RING } });
    expect(wrapper.find("img").attributes("src")).toBe(ADA_FACE);
  });

  it("falls from a missing icon to that face, and then to the mark", async () => {
    const wrapper = mount(ModelMark, {
      props: { row: row({ icon_sha256: "b".repeat(64) }), ring: RING },
    });
    expect(wrapper.find("img").attributes("src")).toBe(icon("b".repeat(64)));

    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").attributes("src")).toBe(ADA_FACE);

    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find(".mmark-initials").text()).toBe("CS");
  });

  // Two errors in ONE task, both from the icon's element. The DOM only catches
  // up on the next tick, so a handler that read the *computed* URL would take
  // the second event as a verdict on Ada's face - a picture it had not yet
  // asked the browser for - and skip straight to the initials.
  it("blames only the load that failed when two errors arrive at once", async () => {
    const wrapper = mount(ModelMark, {
      props: { row: row({ icon_sha256: "b".repeat(64) }), ring: RING },
    });
    const img = wrapper.find("img").element;
    img.dispatchEvent(new Event("error"));
    img.dispatchEvent(new Event("error"));
    await wrapper.vm.$nextTick();

    expect(wrapper.find("img").attributes("src")).toBe(ADA_FACE);
  });

  // A late error from the load that has already been replaced. The `:key` is
  // what makes this harmless: the failed URL got its own element, which keeps
  // that URL as its `src`, so the late event re-reports a failure already on
  // the list and is swallowed as a duplicate. Sharing one element would hand
  // this event the NEW src instead and blacklist a face that never failed.
  // Dispatched on the captured node, because that is exactly what an in-flight
  // request does when it resolves late.
  it("ignores an error from a load it has already moved on from", async () => {
    const wrapper = mount(ModelMark, {
      props: { row: row({ icon_sha256: "b".repeat(64) }), ring: RING },
    });
    const stale = wrapper.find("img").element;
    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").attributes("src")).toBe(ADA_FACE);

    stale.dispatchEvent(new Event("error"));
    await wrapper.vm.$nextTick();
    expect(wrapper.find("img").attributes("src")).toBe(ADA_FACE);
  });

  // The other half of the reset: a recycled mark (the same instance handed a
  // different row) must forget the previous row's failures. The icon store is
  // content-addressed, so rows genuinely share a hash - the URL that failed for
  // one row is the URL the next row wants tried.
  it("tries a failed URL again once it is a different row's", async () => {
    const wrapper = mount(ModelMark, {
      props: { row: row({ icon_sha256: "b".repeat(64) }), ring: RING },
    });
    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").attributes("src")).toBe(ADA_FACE);

    await wrapper.setProps({ ring: { ...RING, id: 9, label: "Bo" } });
    expect(wrapper.find("img").attributes("src")).toBe(icon("b".repeat(64)));
  });

  // Through a PARENT that re-renders, because that is the only way this bug is
  // visible: the shelf calls `ringFor(row)` inline in its `v-for`, so every
  // render hands the mark a new-but-identical ring object, and a reset keyed on
  // object identity would put the mark straight back on the URL that 404ed -
  // on every keystroke in the filter box. Mounting ModelMark alone with a
  // stable prop cannot see it.
  it("keeps the fallback when the parent re-renders with an equal ring", async () => {
    const tick = ref(0);
    const parent = mount(
      defineComponent({
        components: { ModelMark },
        setup: () => ({ tick, row: row({ icon_sha256: "b".repeat(64) }) }),
        // A fresh object literal every render, exactly as `ringFor(row)` is.
        template: `<div :data-tick="tick">
          <ModelMark :row="row"
            :ring="{ type: 'character', id: 4, style: 'dashed', label: 'Ada' }" />
        </div>`,
      }),
    );

    await parent.find("img").trigger("error");
    expect(parent.find("img").attributes("src")).toBe(ADA_FACE);

    tick.value += 1;
    await parent.vm.$nextTick();
    expect(parent.find("img").attributes("src")).toBe(ADA_FACE);
  });

  // A picture set carrying an icon has said that IS its face; the thumbnail is
  // the thing the icon replaces. Borrowing the picture here would put back on
  // the shelf exactly what the sidebar stopped showing.
  const SET_THUMB = `${API_BASE_URL}/picture_sets/5/thumbnail`;
  const SET_RING = {
    type: "set",
    id: 5,
    style: "solid",
    label: "Studio (set)",
    hue: "#fdd835",
    icon: "mdi-star",
    iconHue: "#fdd835",
  };
  // `v-icon` only resolves under the Vuetify plugin, which these mounts do not
  // install. The stub is NAMED and carries its own class, so an assertion on it
  // states "a Vuetify icon was rendered" - a plain span holding the string
  // `mdi-star` would satisfy a text-only assertion just as happily, and that is
  // the shape of the bug. It declares `color` so the binding is observable.
  const ICON_STUB = {
    global: {
      stubs: {
        VIcon: {
          props: ["color"],
          template: `<i class="stub-vicon" :data-color="color"><slot/></i>`,
        },
      },
    },
  };

  it("draws the assigned set's icon instead of its thumbnail", () => {
    const wrapper = mount(ModelMark, {
      props: { row: row(), ring: SET_RING },
      ...ICON_STUB,
    });
    expect(wrapper.find("img").exists()).toBe(false);
    const glyph = wrapper.find("i.stub-vicon.mmark-icon");
    expect(glyph.text()).toBe("mdi-star");
    // In the SET'S OWN colour, which is what the sidebar paints it. The ring's
    // `hue` falls back to a hashed palette entry when a set has no colour, and
    // borrowing that here would put one set on screen in two colours at once.
    expect(glyph.attributes("data-color")).toBe("#fdd835");
  });

  it("leaves a colourless set's icon in theme ink, as the sidebar does", () => {
    const wrapper = mount(ModelMark, {
      props: {
        row: row(),
        ring: { ...SET_RING, iconHue: "", hue: "#8e24aa" },
      },
      ...ICON_STUB,
    });
    expect(
      wrapper.find("i.stub-vicon").attributes("data-color"),
    ).toBeUndefined();
  });

  it("still lets the model's own icon win over the set's", () => {
    // Step 1 of the chain is unchanged: somebody chose that picture for THIS
    // file, which is a more specific statement than the set's icon.
    const wrapper = mount(ModelMark, {
      props: { row: row({ icon_sha256: "c".repeat(64) }), ring: SET_RING },
      ...ICON_STUB,
    });
    expect(wrapper.find("img").attributes("src")).toBe(icon("c".repeat(64)));
    expect(wrapper.find(".mmark-icon").exists()).toBe(false);
  });

  it("follows the set from thumbnail to icon and back", async () => {
    // The set's appearance is edited elsewhere; the shelf reads it off the
    // shared entity list, so the mark has to repaint when the list refreshes
    // rather than only on mount.
    const wrapper = mount(ModelMark, {
      props: { row: row(), ring: { ...SET_RING, icon: "" } },
      ...ICON_STUB,
    });
    expect(wrapper.find("img").attributes("src")).toBe(SET_THUMB);

    await wrapper.setProps({ ring: SET_RING });
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find("i.stub-vicon.mmark-icon").text()).toBe("mdi-star");

    await wrapper.setProps({ ring: { ...SET_RING, icon: "" } });
    expect(wrapper.find("img").exists()).toBe(true);
  });

  it("draws no ring at all where nothing hands it one", () => {
    // Distinct from the `none` style, which is the dashed grey "assigned to
    // nothing" and belongs on a shelf row. A mark in a picker or a dialog is
    // not making a statement about assignment either way.
    expect(mountMark().find(".mmark").classes()).not.toContain("mmark--ring");
  });
});
