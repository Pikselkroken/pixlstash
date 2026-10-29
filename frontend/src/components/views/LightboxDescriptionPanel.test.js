// LightboxDescriptionPanel - ending an edit returns the keyboard to the overlay.
//
// The regression this pins: after Enter saved the description (or Escape
// cancelled the edit), the textarea kept DOM focus, so the lightbox's Ctrl+Z -
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

import LightboxDescriptionPanel from "./LightboxDescriptionPanel.vue";

const IMAGE = { id: 7, description: "a quiet harbour" };

function mountPanel(props = {}) {
  return mount(LightboxDescriptionPanel, {
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

describe("LightboxDescriptionPanel - ending an edit", () => {
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

describe("LightboxDescriptionPanel - Description and Text tabs (#1197)", () => {
  const word = (text, matched = false) => ({
    text,
    box: [0.1, 0.1, 0.1, 0.05],
    matched,
  });
  const LINES = [[word("BAKERY"), word("No.4")], [word("C0FFEE", true)]];

  it("no text: today's Description header, no tablist", () => {
    const w = mountPanel({ textState: "none" });
    expect(w.find('[role="tablist"]').exists()).toBe(false);
    expect(w.find(".section-head").text()).toContain("Description");
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
    expect(w.find(".section-head__count").text()).toBe("18");

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

  it("with a selected word whose text is empty, the strip still offers Copy and Clear", () => {
    const w = mountPanel({
      textState: "read",
      textLines: [[word("")], [word("EXIT")]],
      activeTab: "text",
      selectedWords: [0],
    });
    expect(
      w.findAll(".picture-text-mini").map((b) => b.text()),
    ).toEqual(["Copy", "Clear"]);
  });
});

/** Dispatch a real, bubbling keydown and report whether window heard it. */
function keydownReachesWindow(el, key) {
  const heard = vi.fn();
  window.addEventListener("keydown", heard);
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
  });
  el.dispatchEvent(event);
  window.removeEventListener("keydown", heard);
  return { event, reached: heard.mock.calls.length > 0 };
}

describe("LightboxDescriptionPanel - keyboard in the text (#1197)", () => {
  const word = (text) => ({ text, box: [0.1, 0.1, 0.1, 0.05] });
  // Line 0: A B C, line 1: D E.
  const LINES = [
    [word("A"), word("B"), word("C")],
    [word("D"), word("E")],
  ];
  const mountText = (props = {}) =>
    mountPanel({
      textState: "read",
      textLines: LINES,
      activeTab: "text",
      ...props,
    });
  const wordButtons = (w) => w.findAll(".picture-text-word");

  it("the word list is one tab stop", () => {
    const w = mountText();
    expect(wordButtons(w).map((b) => b.attributes("tabindex"))).toEqual([
      "0",
      "-1",
      "-1",
      "-1",
      "-1",
    ]);
  });

  it("Left/Right move by word, Up/Down by line, and never reach the overlay", async () => {
    const w = mountText();
    const buttons = wordButtons(w);
    buttons[0].element.focus();

    const { event, reached } = keydownReachesWindow(
      buttons[0].element,
      "ArrowRight",
    );
    await w.vm.$nextTick();
    expect(reached).toBe(false);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(buttons[1].element);
    expect(buttons[1].attributes("tabindex")).toBe("0");
    expect(buttons[0].attributes("tabindex")).toBe("-1");

    keydownReachesWindow(buttons[1].element, "ArrowDown");
    await w.vm.$nextTick();
    expect(document.activeElement).toBe(buttons[4].element);

    keydownReachesWindow(buttons[4].element, "ArrowUp");
    await w.vm.$nextTick();
    expect(document.activeElement).toBe(buttons[1].element);

    keydownReachesWindow(buttons[1].element, "ArrowLeft");
    await w.vm.$nextTick();
    expect(document.activeElement).toBe(buttons[0].element);

    // At the edge the key is still kept from the lightbox.
    const edge = keydownReachesWindow(buttons[0].element, "ArrowLeft");
    expect(edge.reached).toBe(false);
    expect(document.activeElement).toBe(buttons[0].element);
  });

  it("other keys are left alone", () => {
    const w = mountText();
    const { reached } = keydownReachesWindow(wordButtons(w)[0].element, "a");
    expect(reached).toBe(true);
  });

  it("arrow keys on a tab switch tabs and never reach the overlay", async () => {
    const w = mountPanel({ textState: "read", textLines: LINES });
    const tabs = w.findAll('[role="tab"]');
    const { event, reached } = keydownReachesWindow(
      tabs[0].element,
      "ArrowRight",
    );
    expect(reached).toBe(false);
    expect(event.defaultPrevented).toBe(true);
    expect(w.emitted("pick-tab")).toEqual([["text"]]);
  });
});

describe("LightboxDescriptionPanel - reading the text again (#1197)", () => {
  const LINES = [[{ text: "EXIT", box: [0.1, 0.1, 0.1, 0.05] }]];
  const readAgainButton = (w) =>
    w.find('[aria-label="Read the text again"]');

  it("keeps the pressed button mounted and focusable while busy", async () => {
    const w = mountPanel({
      textState: "read",
      textLines: LINES,
      activeTab: "text",
    });
    const button = readAgainButton(w);
    button.element.focus();
    await button.trigger("click");
    expect(w.emitted("read-text-again")).toHaveLength(1);

    await w.setProps({ textReadBusy: true });
    const busy = readAgainButton(w);
    expect(busy.element).toBe(button.element);
    expect(busy.attributes("disabled")).toBeUndefined();
    expect(busy.attributes("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(button.element);
    // A second press while busy asks for nothing.
    await busy.trigger("click");
    expect(w.emitted("read-text-again")).toHaveLength(1);
  });

  it("stays on the Text panel, showing that it is reading instead of the old words", () => {
    const w = mountPanel({
      textState: "read",
      textLines: LINES,
      activeTab: "text",
      textReadBusy: true,
    });
    expect(w.find("textarea").exists()).toBe(false);
    expect(w.find(".picture-text").attributes("aria-busy")).toBe("true");
    expect(w.find(".picture-text-reading").exists()).toBe(true);
    expect(w.findAll(".picture-text-word")).toHaveLength(0);
  });
});

describe("LightboxDescriptionPanel - header controls match Tags", () => {
  // v-menu is not registered here; render its activator so the caret mounts.
  const mountHeader = (props) =>
    mount(LightboxDescriptionPanel, {
      props: { image: IMAGE, backendUrl: "http://b.test", textState: "none", ...props },
      global: {
        stubs: {
          "v-menu": { template: '<span><slot name="activator" :props="{}" /></span>' },
        },
      },
    });
  const labels = (w) =>
    w.findAll(".section-head .section-meta-btn").map((b) => b.attributes("aria-label"));

  it("copy comes first, then the wand and its model caret", () => {
    const w = mountHeader();
    expect(labels(w)).toEqual([
      "Copy description",
      expect.stringMatching(/^Regenerate description - /),
      "Regenerate description with a specific model...",
    ]);
    expect(w.find(".section-split").text()).toContain("mdi-auto-fix");
  });

  it("a locked picture keeps copy and drops the wand and caret", () => {
    const w = mountHeader({ locked: true });
    expect(labels(w)).toEqual(["Copy description"]);
  });
});

describe("LightboxDescriptionPanel - the head's title in the description", () => {
  const image = { id: 9, description: "The old man reads. A dog sleeps." };

  it("marks the title's words, ignoring case, only while one is given", async () => {
    const w = mountPanel({ image, highlight: "Old man reads" });
    expect(w.find(".description-mark mark").text()).toBe("old man reads");
    expect(w.find(".description-mark").text()).toBe(image.description);
    await w.setProps({ highlight: "" });
    expect(w.find(".description-mark").exists()).toBe(false);
  });

  it("drops the mark while the caret is in the field", async () => {
    const w = mountPanel({ image, highlight: "Old man reads" });
    await w.find("textarea").trigger("focus");
    expect(w.find(".description-mark").exists()).toBe(false);
  });

  it("a click on the title selects its words in the field and starts the edit", async () => {
    const w = mountPanel({ image });
    w.vm.selectText("Old man reads");
    await w.vm.$nextTick();
    await w.vm.$nextTick();
    const area = w.find("textarea").element;
    expect(document.activeElement).toBe(area);
    expect([area.selectionStart, area.selectionEnd]).toEqual([4, 17]);
    expect(w.vm.isEditingDescription).toBe(true);
    w.unmount();
  });

  it("a locked field still takes the selection, without an edit", async () => {
    const w = mountPanel({ image, locked: true });
    w.vm.selectText("Old man reads");
    await w.vm.$nextTick();
    const area = w.find("textarea").element;
    expect(document.activeElement).toBe(area);
    expect([area.selectionStart, area.selectionEnd]).toEqual([4, 17]);
    expect(w.vm.isEditingDescription).toBe(false);
    w.unmount();
  });

  it("leaves the Text tab for the description and selects there", async () => {
    // The parent switches tabs as the lightbox does: in the emit's handler.
    const w = mountPanel({
      image,
      textState: "read",
      textLines: [[{ text: "EXIT", box: [0.1, 0.1, 0.1, 0.05] }]],
      activeTab: "text",
      "onPick-tab": (tab) => w.setProps({ activeTab: tab }),
    });
    w.vm.selectText("Old man reads");
    expect(w.emitted("pick-tab")).toEqual([["description"]]);
    await w.vm.$nextTick();
    await w.vm.$nextTick();
    const area = w.find("textarea").element;
    expect(document.activeElement).toBe(area);
    expect([area.selectionStart, area.selectionEnd]).toEqual([4, 17]);
    w.unmount();
  });
});
