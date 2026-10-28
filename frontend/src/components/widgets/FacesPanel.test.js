// FacesPanel - the Edit faces popover shared by the grid rail and the lightbox.
//
// It is drawn in place rather than as a Vuetify menu, so it owns the parts a
// menu would have given it: where focus starts, closing on Escape and on a
// press outside, and not closing under a press on its own trigger.

import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VTooltip: { name: "v-tooltip", template: "<span><slot /></span>" },
}));

import FacesPanel from "./FacesPanel.vue";

// The picker, down to what the panel uses of it: a trigger to focus.
const PickerStub = {
  name: "AddToEntityControl",
  props: ["faceId", "label"],
  template: '<button class="picker" :data-face="faceId">{{ label }}</button>',
  methods: {
    focusTrigger() {
      this.$el.focus();
    },
  },
};

const ROWS = [
  { key: 1, id: 1, characterId: 9, name: "Courier", label: "Face 1", hue: "red" },
  { key: 2, id: 2, characterId: null, name: "", label: "Face 2", hue: "blue" },
  { key: 3, id: 3, characterId: null, name: "", label: "Face 3", hue: "gold" },
];

let anchor;

function mountPanel(props = {}) {
  anchor = document.createElement("button");
  anchor.setAttribute("aria-controls", "faces-test");
  document.body.appendChild(anchor);
  return mount(FacesPanel, {
    props: { id: "faces-test", rows: ROWS, anchor, ...props },
    global: { stubs: { AddToEntityControl: PickerStub } },
    attachTo: document.body,
  });
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("FacesPanel", () => {
  it("starts on the first unnamed face, the common case", async () => {
    const w = mountPanel();
    await flushPromises();
    expect(document.activeElement?.dataset.face).toBe("2");
    // An unnamed face's select reads as a question, not as "Unassigned".
    expect(w.findAll(".picker")[1].text()).toBe("Who is this?");
    expect(w.findAll(".picker")[0].text()).toBe("");
  });

  it("starts on the face that was clicked, when one was", async () => {
    mountPanel({ focusKey: 3 });
    await flushPromises();
    expect(document.activeElement?.dataset.face).toBe("3");
  });

  it("never starts on a face without a picker, falling back to close", async () => {
    mountPanel({ rows: [{ ...ROWS[1], key: 8, id: null }] });
    await flushPromises();
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Close");
  });

  it("closes on Escape and gives focus back to the pencil", async () => {
    const w = mountPanel();
    await flushPromises();
    await w.find(".faces-panel").trigger("keydown", { key: "Escape" });
    expect(w.emitted("close")).toHaveLength(1);
    expect(document.activeElement).toBe(anchor);
  });

  it("closes on a press outside, but not on its trigger or a picker's list", async () => {
    const w = mountPanel();
    await flushPromises();
    const press = (el) =>
      el.dispatchEvent(new Event("pointerdown", { bubbles: true }));

    press(anchor);
    const list = document.createElement("div");
    list.className = "ate-menu";
    document.body.appendChild(list);
    press(list);
    press(w.find(".faces-panel__row").element);
    expect(w.emitted("close")).toBeUndefined();

    press(document.body);
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("stops listening once it is gone", async () => {
    const w = mountPanel();
    await flushPromises();
    w.unmount();
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(w.emitted("close")).toBeUndefined();
  });

  it("says there is nothing to name when there are no faces", async () => {
    const w = mountPanel({ rows: [] });
    await flushPromises();
    expect(w.find(".faces-panel__empty").text()).toBe("No faces detected");
    expect(w.find(".faces-panel__count").exists()).toBe(false);
  });
});
