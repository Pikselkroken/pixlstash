// The lightbox's focused layout (design "Lightbox tools live in the inspector
// tab"): the toolbar keeps navigation, filing and view; boxes are drawn only
// while asked about; full screen is the real one, left by Esc first; the
// brush's size steps with − and +.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { useSidebarStore } from "../../stores/useSidebarStore";

import ImageOverlay from "./ImageOverlay.vue";

enableAutoUnmount(afterEach);

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/workflow")) {
    const e = new Error("no workflow");
    e.response = { status: 404 };
    throw e;
  }
  if (typeof url === "string" && url.includes("/faces")) {
    return { data: [{ id: 41, bbox: [100, 100, 300, 200], frame_index: 0 }] };
  }
  if (typeof url === "string" && url.includes("/detections")) {
    return {
      data: [{ label: "dragon", bbox: [400, 400, 800, 800], frame_index: 0 }],
    };
  }
  return { data: [] };
});

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: { get: (...a) => getMock(...a), post: vi.fn(), delete: vi.fn() },
    appendShareToken: (u) => u,
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
  };
});

const STUBS = {
  OverlayTagsPanel: {
    name: "OverlayTagsPanel",
    props: ["boxedTags"],
    setup(_props, { expose }) {
      expose({ refetchPredictions: vi.fn() });
      return {};
    },
    template: "<div class='tags-stub' />",
  },
  OverlayFilmstrip: true,
  // Closing the lightbox ends an edit and resets the copy state through it.
  OverlayDescriptionPanel: {
    setup(_props, { expose }) {
      expose({ cancelEditDescription: vi.fn(), resetCopyState: vi.fn() });
      return {};
    },
    template: "<div />",
  },
  OverlayMetadataPanel: true,
  OverlayEditPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  OverlayActionReceipt: true,
  "v-icon": true,
  // Drawn open, so a menu's rows can be found and pressed.
  "v-menu": {
    template: "<div><slot name='activator' :props='{}' /><slot /></div>",
  },
  "v-tooltip": true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

async function openMeasured() {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [
        { id: 7, format: "jpg", tags: [] },
        { id: 8, format: "jpg", tags: [] },
      ],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
    },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
  await wrapper.setProps({ open: true });
  await flush();
  await flush();
  // An 800×600 stage over a 1600×1200 original: fit 0.5, no letterbox.
  const canvasEl = wrapper.find(".overlay-canvas").element;
  for (const [prop, value] of [
    ["clientWidth", 800],
    ["clientHeight", 600],
  ]) {
    Object.defineProperty(canvasEl, prop, { configurable: true, value });
  }
  const rect = () => ({
    left: 0,
    top: 0,
    right: 800,
    bottom: 600,
    width: 800,
    height: 600,
  });
  canvasEl.getBoundingClientRect = rect;
  wrapper.find(".overlay-media-inner").element.getBoundingClientRect = rect;
  const img = wrapper.find(".overlay-img");
  img.element.getBoundingClientRect = rect;
  for (const [prop, value] of [
    ["naturalWidth", 1600],
    ["naturalHeight", 1200],
    ["clientWidth", 800],
    ["clientHeight", 600],
  ]) {
    Object.defineProperty(img.element, prop, { configurable: true, value });
  }
  await img.trigger("load");
  await flush();
  return wrapper;
}

function press(key, init = {}, type = "keydown") {
  const event = new KeyboardEvent(type, {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  (init.target ?? window).dispatchEvent(event);
  return event;
}

/** The boxes actually drawn: the rest are in the DOM but `display: none`. */
const shownBoxes = (wrapper) =>
  wrapper
    .findAll(".face-bbox-overlay")
    .filter((box) => !(box.attributes("style") || "").includes("display: none"))
    .map((box) => box.find(".face-bbox-label").text());

let fullscreenElement = null;

beforeEach(() => {
  setActivePinia(createPinia());
  useSidebarStore().statsOpen = true;
  // jsdom has no Fullscreen API: a stand-in that answers as a browser does.
  fullscreenElement = null;
  Object.defineProperty(document, "fullscreenElement", {
    configurable: true,
    get: () => fullscreenElement,
  });
  document.documentElement.requestFullscreen = vi.fn(async () => {
    fullscreenElement = document.documentElement;
    document.dispatchEvent(new Event("fullscreenchange"));
  });
  document.exitFullscreen = vi.fn(async () => {
    fullscreenElement = null;
    document.dispatchEvent(new Event("fullscreenchange"));
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the lightbox toolbar is navigation, filing and view", () => {
  it("leaves the picture's tools to the inspector", async () => {
    const wrapper = await openMeasured();
    const bar = wrapper.find(".overlay-topbar");
    for (const gone of [
      '[aria-label="Face boxes"]',
      '[aria-label="Object detection boxes"]',
      '[aria-label="Draw a face box"]',
      '[aria-keyshortcuts="["]',
      '[data-testid="overlay-upscale"]',
      ".overlay-comfy-activator",
    ]) {
      expect(bar.find(gone).exists(), gone).toBe(false);
    }
    expect(bar.find(".zoom-btn").exists()).toBe(true);
    expect(bar.find(".overlay-compare-btn").exists()).toBe(true);
    expect(bar.find(".overlay-fullscreen-btn").exists()).toBe(true);
  });
});

describe("boxes are drawn while asked about, never left on", () => {
  it("draws none until asked", async () => {
    const wrapper = await openMeasured();
    expect(wrapper.findAll(".face-bbox-overlay").length).toBe(2);
    expect(shownBoxes(wrapper)).toEqual([]);
  });

  it("draws a face's box while its crop in Info is pointed at", async () => {
    const wrapper = await openMeasured();
    const chip = wrapper.find(".face-chip");
    expect(chip.text()).toBe("Unknown");
    await chip.trigger("mouseenter");
    expect(shownBoxes(wrapper)).toEqual(["Face 1"]);
    await chip.trigger("mouseleave");
    expect(shownBoxes(wrapper)).toEqual([]);
  });

  it("lets a face's box go when the inspector shuts under the pointer", async () => {
    const wrapper = await openMeasured();
    await wrapper.find(".face-chip").trigger("mouseenter");
    expect(shownBoxes(wrapper)).toEqual(["Face 1"]);
    press("s");
    await flush();
    expect(shownBoxes(wrapper)).toEqual([]);
  });

  it("draws a detected tag's box while the tag is pointed at", async () => {
    const wrapper = await openMeasured();
    const tags = wrapper.findComponent({ name: "OverlayTagsPanel" });
    expect([...tags.props("boxedTags")]).toEqual(["dragon"]);
    tags.vm.$emit("hover-tag", "Dragon");
    await flush();
    expect(shownBoxes(wrapper)).toEqual(["dragon"]);
    tags.vm.$emit("hover-tag", null);
    await flush();
    expect(shownBoxes(wrapper)).toEqual([]);
  });

  it("draws every box while Shift is held, but not for a capital typed", async () => {
    const wrapper = await openMeasured();
    press("Shift");
    await flush();
    expect(shownBoxes(wrapper)).toEqual(["Face 1", "dragon"]);
    press("Shift", {}, "keyup");
    await flush();
    expect(shownBoxes(wrapper)).toEqual([]);

    const input = document.createElement("input");
    document.body.appendChild(input);
    press("Shift", { target: input });
    await flush();
    expect(shownBoxes(wrapper)).toEqual([]);
    input.remove();
  });

  it("rings a face's crop while the face on the picture is pointed at", async () => {
    const wrapper = await openMeasured();
    const canvas = wrapper.find(".overlay-canvas").element;
    // The face is [100,100,300,200] of 1600×1200, at fit 0.5: 50..150, 50..100.
    canvas.dispatchEvent(
      new MouseEvent("mousemove", { bubbles: true, clientX: 100, clientY: 75 }),
    );
    await flush();
    expect(wrapper.find(".face-chip").classes()).toContain("face-chip--ringed");
    canvas.dispatchEvent(
      new MouseEvent("mousemove", { bubbles: true, clientX: 600, clientY: 75 }),
    );
    await flush();
    expect(wrapper.find(".face-chip").classes()).not.toContain(
      "face-chip--ringed",
    );
  });

  it("edits faces in a panel over the sidebar, not an inline grid", async () => {
    const wrapper = await openMeasured();
    const section = wrapper.find(".sidebar-section--faces");
    expect(section.find('[aria-label="More face actions"]').exists()).toBe(false);
    await section.find('[aria-label="Edit faces"]').trigger("click");
    const panel = section.find(".faces-panel");
    expect(panel.exists()).toBe(true);
    expect(panel.classes()).toContain("faces-panel--on-dark");
    expect(panel.findAll(".faces-panel__row")).toHaveLength(1);
    // Pointing at a row draws its face's box, as its crop does.
    await panel.find(".faces-panel__row").trigger("mouseenter");
    expect(shownBoxes(wrapper)).toEqual(["Face 1"]);
    await panel.find(".faces-panel__row").trigger("mouseleave");
    expect(shownBoxes(wrapper)).toEqual([]);
  });

  it("keeps the panel while a face row's new-person dialog is up", async () => {
    const wrapper = await openMeasured();
    await wrapper.find('[aria-label="Edit faces"]').trigger("click");
    wrapper
      .find(".faces-panel")
      .findComponent({ name: "AddToEntityControl" })
      .vm.$emit("create", "Ranger");
    await flush();
    // A press in the dialog is outside the panel; the row it came from must
    // still be there for focus to return to.
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await flush();
    expect(wrapper.find(".faces-panel").exists()).toBe(true);
  });

  it("draws a face box from the faces panel's footer", async () => {
    const wrapper = await openMeasured();
    await wrapper.find('[aria-label="Edit faces"]').trigger("click");
    const draw = wrapper
      .findAll(".faces-panel .tbm-footer button")
      .find((item) => item.text().includes("Draw a face box"));
    await draw.trigger("click");
    expect(wrapper.find(".overlay-draw-layer").exists()).toBe(true);
    expect(wrapper.find(".faces-panel").exists()).toBe(false);
  });
});

describe("full screen", () => {
  it("F enters the real full screen, which hides the frame and says how out", async () => {
    const wrapper = await openMeasured();
    press("f");
    await flush();
    expect(document.documentElement.requestFullscreen).toHaveBeenCalledTimes(1);
    expect(document.documentElement.classList).toContain("lightbox-fullscreen");
    expect(wrapper.find(".overlay-shell").classes()).toContain(
      "overlay-shell--fullscreen",
    );
    expect(wrapper.find(".overlay-fullscreen-notice").exists()).toBe(true);
    // The inspector goes too, without touching the rail's remembered flag.
    expect(wrapper.vm.inspectorShown).toBe(false);
    expect(useSidebarStore().statsOpen).toBe(true);
  });

  it("Esc leaves full screen first and the lightbox second", async () => {
    const wrapper = await openMeasured();
    press("f");
    await flush();
    press("Escape");
    await flush();
    expect(document.exitFullscreen).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(document.documentElement.classList).not.toContain(
      "lightbox-fullscreen",
    );
    press("Escape");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("brings the toolbar down at the top edge, with a labelled way out", async () => {
    const wrapper = await openMeasured();
    press("f");
    await flush();
    const shell = wrapper.find(".overlay-shell");
    expect(shell.classes()).not.toContain("overlay-shell--bar-down");
    shell.element.dispatchEvent(
      new MouseEvent("mousemove", { bubbles: true, clientX: 300, clientY: 10 }),
    );
    await flush();
    expect(shell.classes()).toContain("overlay-shell--bar-down");
    const exit = wrapper.find(".overlay-fullscreen-exit");
    expect(exit.text()).toContain("Exit full screen");
    expect(exit.text()).toContain("Esc");
  });

  it("brings the edge tab out at the right edge", async () => {
    const wrapper = await openMeasured();
    press("f");
    await flush();
    const tab = wrapper.find('[data-testid="inspector-edge-tab"]');
    const shell = wrapper.find(".overlay-shell");
    const moveTo = async (clientX) => {
      shell.element.dispatchEvent(
        new MouseEvent("mousemove", { bubbles: true, clientX, clientY: 300 }),
      );
      await flush();
    };
    await moveTo(window.innerWidth / 2);
    expect(tab.isVisible()).toBe(false);
    await moveTo(window.innerWidth - 10);
    expect(tab.isVisible()).toBe(true);
    // One arrival, one nudge: moving about inside the zone is not another.
    const nudges = wrapper.vm.edgeTabNudge;
    await moveTo(window.innerWidth - 20);
    expect(wrapper.vm.edgeTabNudge).toBe(nudges);
    await tab.trigger("click");
    expect(wrapper.vm.inspectorShown).toBe(true);
  });

  it("I calls the inspector back over the full-screen picture", async () => {
    const wrapper = await openMeasured();
    press("f");
    await flush();
    press("i");
    await flush();
    expect(wrapper.vm.inspectorShown).toBe(true);
    press("i");
    await flush();
    expect(wrapper.vm.inspectorShown).toBe(false);
  });

  it("is left when the lightbox closes", async () => {
    const wrapper = await openMeasured();
    press("f");
    await flush();
    await wrapper.setProps({ open: false });
    await flush();
    expect(document.exitFullscreen).toHaveBeenCalledTimes(1);
    expect(document.documentElement.classList).not.toContain(
      "lightbox-fullscreen",
    );
  });
});

describe("the brush's size", () => {
  it("steps with − and + (= too) only while a brush or eraser is in hand", async () => {
    const wrapper = await openMeasured();
    wrapper.vm.sidebarTab = "edit";
    await flush();
    const paint = wrapper.vm.paint;
    paint.setTool("rect");
    press("=");
    expect(paint.brushSize.value).toBe(24);
    paint.setTool("brush");
    press("=");
    expect(paint.brushSize.value).toBe(29);
    press("+");
    expect(paint.brushSize.value).toBe(34);
    press("-");
    expect(paint.brushSize.value).toBe(29);
  });
});
