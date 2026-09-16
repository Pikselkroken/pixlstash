// OverlayDescriptionPanel - ending an edit returns the keyboard to the overlay.
//
// The regression this pins: after Enter saved the description (or Escape
// cancelled the edit), the textarea kept DOM focus, so the overlay's Ctrl+Z -
// which rightly defers to typing targets - stayed dead until a click. Ending
// an edit must blur the field and tell the parent, which refocuses its canvas.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const patchPicture = vi.fn();
const resetPictureDescription = vi.fn();
const listTaggers = vi.fn();

vi.mock("../../api/pictures", () => ({
  patchPicture: (...a) => patchPicture(...a),
  resetPictureDescription: (...a) => resetPictureDescription(...a),
}));

vi.mock("../../api/taggers", () => ({
  listTaggers: (...a) => listTaggers(...a),
}));

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return { isReadOnly: ref(false) };
});

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VProgressCircular: { name: "v-progress-circular", template: "<i></i>" },
  VTooltip: { name: "v-tooltip", template: "<span></span>" },
}));

import OverlayDescriptionPanel from "./OverlayDescriptionPanel.vue";

const IMAGE = { id: 7, description: "a quiet harbour" };

function mountPanel(props = {}) {
  return mount(OverlayDescriptionPanel, {
    props: { image: IMAGE, backendUrl: "http://b.test", ...props },
    attachTo: document.body,
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  patchPicture.mockResolvedValue({});
  listTaggers.mockResolvedValue([]);
});

describe("OverlayDescriptionPanel - ending an edit", () => {
  it("saves on Enter, blurs the field and signals editing-finished", async () => {
    const w = mountPanel();
    const area = w.find("textarea");
    await area.trigger("focus");
    expect(w.vm.isEditingDescription).toBe(true);

    await area.setValue("a louder harbour");
    await area.trigger("keydown.enter");
    await Promise.resolve();
    await w.vm.$nextTick();

    expect(patchPicture).toHaveBeenCalledWith(
      7,
      { description: "a louder harbour" },
    );
    expect(w.emitted("update-description")).toBeTruthy();
    expect(w.emitted("editing-finished")).toBeTruthy();
    expect(document.activeElement).not.toBe(area.element);
  });

  it("cancel blurs the field and signals editing-finished too", async () => {
    const w = mountPanel();
    const area = w.find("textarea");
    await area.trigger("focus");
    w.vm.cancelEditDescription();
    await w.vm.$nextTick();

    expect(w.emitted("editing-finished")).toBeTruthy();
    expect(w.vm.isEditingDescription).toBe(false);
    expect(document.activeElement).not.toBe(area.element);
  });

  it("adopts an external description change while not editing", async () => {
    // This is the panel half of undo/redo reaching an open overlay: the
    // parent refetches metadata and the prop moves; the field must follow.
    const w = mountPanel();
    await w.setProps({ image: { id: 7, description: "restored text" } });
    await w.vm.$nextTick();
    expect(w.find("textarea").element.value).toBe("restored text");
  });
});

describe("OverlayDescriptionPanel - Description and Text tabs (#1197)", () => {
  const word = (text, matched = false) => ({
    text,
    box: [0.1, 0.1, 0.1, 0.05],
    matched,
  });
  const LINES = [[word("BAKERY"), word("No.4")], [word("C0FFEE", true)]];

  it("no text: today's Description header, no tablist", () => {
    const w = mountPanel({ textState: "none" });
    expect(w.find('[role="tablist"]').exists()).toBe(false);
    expect(w.find(".section-header").text()).toContain("Description");
  });

  it("read with no words: still no tablist", () => {
    const w = mountPanel({ textState: "read", textLines: [[]] });
    expect(w.find('[role="tablist"]').exists()).toBe(false);
  });

  it("pending: a Text tab that cannot be opened", () => {
    const w = mountPanel({ textState: "pending" });
    const tabs = w.findAll('[role="tab"]');
    expect(tabs).toHaveLength(2);
    expect(tabs[1].attributes("disabled")).toBeDefined();
    expect(tabs[0].attributes("aria-selected")).toBe("true");
  });

  it("text found: both tabs, and picking Text asks the parent", async () => {
    const w = mountPanel({ textState: "read", textLines: LINES });
    const tabs = w.findAll('[role="tab"]');
    expect(tabs[1].attributes("disabled")).toBeUndefined();
    await tabs[1].trigger("click");
    expect(w.emitted("pick-tab")).toEqual([["text"]]);
  });

  it("the Text panel lists one button per word, marks matches, and reports shift", async () => {
    const w = mountPanel({
      textState: "read",
      textLines: LINES,
      activeTab: "text",
      selectedWords: [0],
    });
    expect(w.find("textarea").exists()).toBe(false);
    const words = w.findAll(".picture-text-word");
    expect(words.map((b) => b.text())).toEqual(["BAKERY", "No.4", "C0FFEE"]);
    expect(words[2].classes()).toContain("picture-text-word--match");
    expect(words[0].attributes("aria-pressed")).toBe("true");
    expect(w.find(".picture-text-selection").text()).toContain("BAKERY");
    // The header count is the whole text, lines joined by a newline.
    expect(w.find(".section-meta").text()).toBe(String("BAKERY No.4\nC0FFEE".length));

    await words[1].trigger("click", { shiftKey: true });
    expect(w.emitted("select-word")).toEqual([[1, true]]);
  });

  it("with nothing selected the strip is the hint", () => {
    const w = mountPanel({
      textState: "read",
      textLines: LINES,
      activeTab: "text",
    });
    expect(w.find(".picture-text-selection").text()).toContain(
      "Click a word to find it in the picture",
    );
  });
});
