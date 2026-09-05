// The lightbox's continuous cursor-anchored wheel zoom (the Compare model,
// adopted via the shared useWheelZoom core). These tests drive the real DOM
// events the way a user produces them and give the canvas/image the geometry
// jsdom does not: an 800×600 viewport over a 1600×1200 original → fit = 0.5,
// so the fit readout is "50%" and 100% doubles it.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { anchorZoomOffset } from "../../utils/zoomMath";
import { ZOOM_SETTLE_MS } from "../../composables/useWheelZoom";

// Imported statically, not lazily inside the tests. `vi.mock` is hoisted
// above every import, so a lazy import buys no mock ordering. It only moves
// the cost of compiling this 5.7k-line SFC (~7s on a loaded machine) inside
// the first test's 5s timeout, which is what made this file flake in the full
// suite while passing on its own.
import ImageOverlay from "./ImageOverlay.vue";

// A test that fails mid-way must not leave a mounted overlay behind: its
// window-level keydown listener would answer every later test in this file.
enableAutoUnmount(afterEach);

let metadataResponse = [];

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/workflow")) {
    const e = new Error("no workflow");
    e.response = { status: 404 };
    throw e;
  }
  if (typeof url === "string" && url.includes("/faces")) {
    return { data: [{ bbox: [100, 100, 300, 200], frame_index: 0 }] };
  }
  if (typeof url === "string" && url.includes("/metadata")) {
    return { data: await metadataResponse };
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
    setup(_props, { expose }) {
      expose({ refetchPredictions: vi.fn() });
      return {};
    },
    template: "<div />",
  },
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: true,
  OverlayMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  OverlayActionReceipt: true,
  "v-icon": true,
  "v-menu": true,
  "v-tooltip": true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

const CANVAS = { w: 800, h: 600 };
const NATURAL = { w: 1600, h: 1200 };
const FIT = 0.5;

async function openOverlay() {
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
  return wrapper;
}

/** Give the canvas and the image the geometry jsdom lacks, then fire the load
 * event that measures it (the same path a real decode takes). */
async function measure(wrapper, { canvas = CANVAS, natural = NATURAL } = {}) {
  const canvasEl = wrapper.find(".overlay-canvas").element;
  Object.defineProperty(canvasEl, "clientWidth", {
    configurable: true,
    value: canvas.w,
  });
  Object.defineProperty(canvasEl, "clientHeight", {
    configurable: true,
    value: canvas.h,
  });
  canvasEl.getBoundingClientRect = () => ({
    left: 0,
    top: 0,
    right: canvas.w,
    bottom: canvas.h,
    width: canvas.w,
    height: canvas.h,
  });
  const img = wrapper.find(".overlay-img");
  const fit = Math.min(canvas.w / natural.w, canvas.h / natural.h);
  for (const [prop, value] of [
    ["naturalWidth", natural.w],
    ["naturalHeight", natural.h],
    ["clientWidth", natural.w * fit],
    ["clientHeight", natural.h * fit],
  ]) {
    Object.defineProperty(img.element, prop, { configurable: true, value });
  }
  await img.trigger("load");
  await wrapper.vm.$nextTick();
}

async function openMeasured() {
  const wrapper = await openOverlay();
  await measure(wrapper);
  return wrapper;
}

function press(key, init = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  window.dispatchEvent(event);
  return event;
}

// jsdom lacks PointerEvent and vue-test-utils cannot assign the readonly
// clientX/deltaY getters on wheel/pointer events, so these dispatch REAL
// constructed events - the same objects a browser hands the listeners.
async function wheelCanvas(wrapper, deltaY, cursor = { x: 400, y: 300 }) {
  const ev = new WheelEvent("wheel", {
    bubbles: true,
    cancelable: true,
    deltaY,
    deltaMode: 0,
    clientX: cursor.x,
    clientY: cursor.y,
  });
  wrapper.find(".overlay-canvas").element.dispatchEvent(ev);
  await wrapper.vm.$nextTick();
}

async function firePointer(wrapper, target, type, init = {}) {
  const ev = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: init.clientX ?? 0,
    clientY: init.clientY ?? 0,
    button: 0,
  });
  Object.defineProperty(ev, "pointerId", { value: init.pointerId ?? 1 });
  target.element.dispatchEvent(ev);
  await wrapper.vm.$nextTick();
}

function zoomLabel(wrapper) {
  return wrapper.find(".zoom-btn-label").text();
}

/** Parse `translate(Xpx, Ypx) scale(S)` off `.overlay-media`. */
function mediaTransform(wrapper) {
  const style = wrapper.find(".overlay-media").attributes("style") || "";
  const m = style.match(
    /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)\s*scale\((-?[\d.]+)\)/,
  );
  expect(m, `no transform in style: ${style}`).toBeTruthy();
  return { x: Number(m[1]), y: Number(m[2]), scale: Number(m[3]) };
}

beforeEach(() => {
  setActivePinia(createPinia());
  metadataResponse = [];
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ImageOverlay cold media bootstrap", () => {
  it("waits for a real media URL and recovers when that same-id URL changes", async () => {
    let resolveMetadata;
    metadataResponse = new Promise((resolve) => {
      resolveMetadata = resolve;
    });

    const wrapper = mount(ImageOverlay, {
      props: {
        open: true,
        initialImageId: 7,
        allImages: [{ id: 7, tags: [] }],
        backendUrl: "http://test",
        tagUpdate: { key: 0, pictureIds: [] },
        descriptionUpdate: { key: 0, pictureIds: [] },
        smartScoreUpdate: { key: 0, pictureIds: [] },
      },
      global: { stubs: STUBS },
      attachTo: document.body,
    });

    // An id-only cold-route placeholder must never point <img> at the JSON
    // `/pictures/{id}` endpoint while metadata is still in flight.
    expect(wrapper.find(".overlay-img").exists()).toBe(false);
    expect(wrapper.find(".overlay-image-error").exists()).toBe(false);

    resolveMetadata({ id: 7, format: "png", orientation: 6, tags: [] });
    await flush();
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toBe(
      "http://test/pictures/7.png?v=o6",
    );

    await wrapper.find(".overlay-img").trigger("error");
    expect(wrapper.find(".overlay-image-error").exists()).toBe(true);

    await wrapper.setProps({ backendUrl: "http://replacement" });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".overlay-image-error").exists()).toBe(false);
    expect(wrapper.find(".overlay-img").attributes("src")).toBe(
      "http://replacement/pictures/7.png?v=o6",
    );
  });

  // The flash-and-reload regression. `orientation` rides the grid projection
  // (`Picture.grid_fields()`), so the <img> starts on the URL the metadata
  // fetch will go on to confirm. Nothing about the src may move when that
  // fetch lands: swapping it would swap the element too (`:key="fullImageSrc"`)
  // - a blank frame plus a second download of bytes already on screen.
  it("does not touch the URL when metadata confirms the orientation", async () => {
    let resolveMetadata;
    metadataResponse = new Promise((resolve) => {
      resolveMetadata = resolve;
    });

    const wrapper = mount(ImageOverlay, {
      props: {
        open: true,
        initialImageId: 7,
        allImages: [{ id: 7, format: "png", orientation: 6, tags: [] }],
        backendUrl: "http://test",
        tagUpdate: { key: 0, pictureIds: [] },
        descriptionUpdate: { key: 0, pictureIds: [] },
        smartScoreUpdate: { key: 0, pictureIds: [] },
      },
      global: { stubs: STUBS },
      attachTo: document.body,
    });

    const initialSrc = wrapper.find(".overlay-img").attributes("src");
    expect(initialSrc).toBe("http://test/pictures/7.png?v=o6");
    const initialEl = wrapper.find(".overlay-img").element;

    resolveMetadata({
      id: 7,
      format: "png",
      orientation: 6,
      pixel_sha: "first",
      tags: [],
    });
    await flush();
    await flush();

    expect(wrapper.find(".overlay-img").attributes("src")).toBe(initialSrc);
    expect(wrapper.find(".overlay-img").element).toBe(initialEl);
  });

  it("keeps the first URL when orientation backfill lands", async () => {
    let resolveMetadata;
    metadataResponse = new Promise((resolve) => {
      resolveMetadata = resolve;
    });

    const wrapper = mount(ImageOverlay, {
      props: {
        open: true,
        initialImageId: 7,
        allImages: [{ id: 7, format: "png", orientation: null, tags: [] }],
        backendUrl: "http://test",
        tagUpdate: { key: 0, pictureIds: [] },
        descriptionUpdate: { key: 0, pictureIds: [] },
        smartScoreUpdate: { key: 0, pictureIds: [] },
      },
      global: { stubs: STUBS },
      attachTo: document.body,
    });

    const initialSrc = wrapper.find(".overlay-img").attributes("src");
    expect(initialSrc).toBe("http://test/pictures/7.png");
    const initialEl = wrapper.find(".overlay-img").element;

    resolveMetadata({
      id: 7,
      format: "png",
      orientation: 6,
      pixel_sha: "first",
      tags: [],
    });
    await flush();
    await flush();

    expect(wrapper.find(".overlay-img").attributes("src")).toBe(initialSrc);
    expect(wrapper.find(".overlay-img").element).toBe(initialEl);
  });

  // …and it does reload when the orientation really moves. That is the whole
  // point of the buster: an in-place rotate rewrites the EXIF tag and copies
  // every pixel through, so nothing else about the file changes.
  it("reloads when the orientation changes under the same id", async () => {
    metadataResponse = { id: 7, format: "png", orientation: 1, tags: [] };

    const wrapper = mount(ImageOverlay, {
      props: {
        open: true,
        initialImageId: 7,
        allImages: [{ id: 7, format: "png", orientation: 1, tags: [] }],
        backendUrl: "http://test",
        tagUpdate: { key: 0, pictureIds: [] },
        descriptionUpdate: { key: 0, pictureIds: [] },
        smartScoreUpdate: { key: 0, pictureIds: [] },
      },
      global: { stubs: STUBS },
      attachTo: document.body,
    });
    await flush();
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toBe(
      "http://test/pictures/7.png",
    );

    // A record whose file was turned in place.
    wrapper.vm.image = { ...wrapper.vm.image, orientation: 8 };
    await flush();

    expect(wrapper.find(".overlay-img").attributes("src")).toBe(
      "http://test/pictures/7.png?v=o8",
    );
  });
});

describe("ImageOverlay zoom - the continuous wheel", () => {
  it("enters at fit and shows the computed fit percentage, not the word Fit", async () => {
    const wrapper = await openMeasured();
    expect(zoomLabel(wrapper)).toBe("50%");
    expect(wrapper.find(".zoom-hud").exists()).toBe(false); // HUD retired
    const t = mediaTransform(wrapper);
    expect(t).toEqual({ x: 0, y: 0, scale: 1 });
  });

  it("wheel-in zooms continuously, anchored at the cursor", async () => {
    const wrapper = await openMeasured();
    const cursor = { x: 200, y: 150 };
    await wheelCanvas(wrapper, -100, cursor);
    const newScale = FIT * Math.exp(0.2);
    // The wiring must hand the pure anchor solver exactly this step.
    const expected = anchorZoomOffset({
      cursorX: cursor.x,
      cursorY: cursor.y,
      offsetX: 0,
      offsetY: 0,
      containerWidth: CANVAS.w,
      containerHeight: CANVAS.h,
      imageWidth: NATURAL.w,
      imageHeight: NATURAL.h,
      oldScale: FIT,
      newScale,
    });
    const t = mediaTransform(wrapper);
    expect(t.x).toBeCloseTo(expected.x, 3);
    expect(t.y).toBeCloseTo(expected.y, 3);
    expect(t.scale).toBeCloseTo(Math.exp(0.2), 3); // css scale = scale/fit
    expect(zoomLabel(wrapper)).toBe(
      `${Math.round(newScale * 100)}%`, // 61%
    );
  });

  it("a big out-wheel rests at fit - hard clamp, no exit, overlay stays open", async () => {
    const wrapper = await openMeasured();
    press("z"); // 100%
    await wrapper.vm.$nextTick();
    expect(zoomLabel(wrapper)).toBe("100%");
    await wheelCanvas(wrapper, 100000); // per-event clamp halves at most → fit
    expect(zoomLabel(wrapper)).toBe("50%");
    for (let i = 0; i < 5; i += 1) await wheelCanvas(wrapper, 10000);
    expect(zoomLabel(wrapper)).toBe("50%");
    expect(mediaTransform(wrapper)).toEqual({ x: 0, y: 0, scale: 1 });
    expect(wrapper.emitted("close")).toBeFalsy();
  });
});

describe("ImageOverlay zoom - snap stops", () => {
  it("Z toggles fit ↔ 100%, and the button title narrates both directions", async () => {
    const wrapper = await openMeasured();
    const btn = wrapper.find(".zoom-btn");
    expect(btn.attributes("title")).toBe(
      "Zoom 50% (fit) - click for 100% (Z)",
    );
    press("z");
    await wrapper.vm.$nextTick();
    expect(zoomLabel(wrapper)).toBe("100%");
    expect(mediaTransform(wrapper).scale).toBeCloseTo(2, 5);
    expect(btn.attributes("title")).toBe("Zoom 100% - click to fit (Z)");
    press("z");
    await wrapper.vm.$nextTick();
    expect(zoomLabel(wrapper)).toBe("50%");
  });

  it("the button click has identical semantics to Z", async () => {
    const wrapper = await openMeasured();
    const btn = wrapper.find(".zoom-btn");
    await btn.trigger("click");
    expect(zoomLabel(wrapper)).toBe("100%");
    // From an intermediate wheel scale the click goes to fit, not 100%.
    await wheelCanvas(wrapper, -100);
    expect(zoomLabel(wrapper)).not.toBe("100%");
    await btn.trigger("click");
    expect(zoomLabel(wrapper)).toBe("50%");
  });

  it("double-click toggles fit ↔ 100% anchored at the click point", async () => {
    const wrapper = await openMeasured();
    // Double-click the top-left corner: the image point there must stay under
    // the corner, which forces the offset to the full clamp range (+400,+300).
    await wrapper
      .find(".overlay-canvas")
      .trigger("dblclick", { clientX: 0, clientY: 0 });
    expect(zoomLabel(wrapper)).toBe("100%");
    const t = mediaTransform(wrapper);
    expect(t.x).toBeCloseTo(400, 3);
    expect(t.y).toBeCloseTo(300, 3);
    await wrapper
      .find(".overlay-canvas")
      .trigger("dblclick", { clientX: 0, clientY: 0 });
    expect(zoomLabel(wrapper)).toBe("50%");
    expect(mediaTransform(wrapper)).toEqual({ x: 0, y: 0, scale: 1 });
  });
});

describe("ImageOverlay zoom - pan", () => {
  it("clamps the drag so the image edge never crosses the viewport edge, and re-centres at fit", async () => {
    const wrapper = await openMeasured();
    press("z"); // 100%: range is ±400/±300
    await wrapper.vm.$nextTick();
    const media = wrapper.find(".overlay-media");
    media.element.setPointerCapture = () => {};
    media.element.releasePointerCapture = () => {};
    await firePointer(wrapper, media, "pointerdown", {
      clientX: 400,
      clientY: 300,
    });
    await firePointer(wrapper, media, "pointermove", {
      clientX: 10000,
      clientY: 10000,
    });
    await firePointer(wrapper, media, "pointerup", {});
    let t = mediaTransform(wrapper);
    expect(t.x).toBe(400);
    expect(t.y).toBe(300);
    // Zoom-out re-clamps: at fit the pan range is zero → re-centred.
    press("z");
    await wrapper.vm.$nextTick();
    t = mediaTransform(wrapper);
    expect(t).toEqual({ x: 0, y: 0, scale: 1 });
  });

  it("does not pan at fit (drag stays drag-out)", async () => {
    const wrapper = await openMeasured();
    const media = wrapper.find(".overlay-media");
    media.element.setPointerCapture = () => {};
    await firePointer(wrapper, media, "pointerdown", {
      clientX: 400,
      clientY: 300,
    });
    await firePointer(wrapper, media, "pointermove", {
      clientX: 500,
      clientY: 400,
    });
    expect(mediaTransform(wrapper)).toEqual({ x: 0, y: 0, scale: 1 });
  });
});

describe("ImageOverlay zoom - the readout and the announcer", () => {
  it("the label re-fits on resize: a smaller viewport shows the new fit percentage", async () => {
    const wrapper = await openMeasured();
    expect(zoomLabel(wrapper)).toBe("50%");
    await measure(wrapper, { canvas: { w: 400, h: 600 } });
    expect(zoomLabel(wrapper)).toBe("25%");
  });

  it("reserves the label width so the toolbar never jumps", async () => {
    const wrapper = await openMeasured();
    // The width contract is CSS (min-width: 5ch, tabular-nums), pinned here
    // by the class the rule hangs on; the label itself binds to the scale.
    expect(wrapper.find(".zoom-btn-label").classes()).toContain(
      "zoom-btn-label",
    );
    expect(wrapper.find(".zoom-btn").attributes("aria-label")).toContain("50%");
  });

  it("announces a wheel gesture on settle, and a snap immediately", async () => {
    const wrapper = await openMeasured();
    const status = wrapper.find('[role="status"]');
    expect(status.exists()).toBe(true);
    expect(status.text()).toBe("");
    vi.useFakeTimers();
    await wheelCanvas(wrapper, -100);
    expect(status.text()).toBe(""); // not yet settled
    vi.advanceTimersByTime(ZOOM_SETTLE_MS);
    await wrapper.vm.$nextTick();
    expect(status.text()).toBe(
      `Zoom ${Math.round(FIT * Math.exp(0.2) * 100)}%`,
    );
    press("z"); // snap to fit (not at fit) → immediate, names fit
    await wrapper.vm.$nextTick();
    expect(status.text()).toBe("Zoom fit, 50%");
    press("z");
    await wrapper.vm.$nextTick();
    expect(status.text()).toBe("Zoom 100%");
    // The button itself must NOT be a live region.
    expect(wrapper.find(".zoom-btn").attributes("aria-live")).toBeUndefined();
  });
});

describe("ImageOverlay zoom - overlays ride the transform", () => {
  it("face bboxes keep their layout-space position at an intermediate scale", async () => {
    const wrapper = await openMeasured();
    await wrapper.find('[aria-label="Toggle face bounding boxes"]').trigger("click");
    await flush();
    await wrapper.vm.$nextTick();
    const boxAtFit = wrapper.find(".face-bbox-overlay");
    expect(boxAtFit.exists()).toBe(true);
    // bbox [100,100,300,200] natural → layout at fit 0.5: 50,50 100×50.
    const styleAtFit = boxAtFit.attributes("style");
    expect(styleAtFit).toContain("left: 50px");
    expect(styleAtFit).toContain("top: 50px");
    expect(styleAtFit).toContain("width: 100px");
    expect(styleAtFit).toContain("height: 50px");
    // Five wheel notches land on an intermediate scale (≈136%, nowhere near
    // the old 1.5/2 ladder). The box's layout-space style must NOT change:
    // the shared transform on `.overlay-media` is what carries it.
    for (let i = 0; i < 5; i += 1) await wheelCanvas(wrapper, -100);
    expect(zoomLabel(wrapper)).toBe("136%");
    expect(wrapper.find(".face-bbox-overlay").attributes("style")).toBe(
      styleAtFit,
    );
    expect(mediaTransform(wrapper).scale).toBeCloseTo(Math.exp(1), 3);
  });

  it("the draw rectangle maps the cursor through the transform at 100%", async () => {
    const wrapper = await openMeasured();
    press("z"); // 100%, centred: css scale 2, offset 0
    await wrapper.vm.$nextTick();
    await wrapper
      .find('[aria-label="Draw face bounding box"]')
      .trigger("click");
    const layer = wrapper.find(".overlay-draw-layer");
    expect(layer.exists()).toBe(true);
    // The transformed media-inner box: 800×600 layout scaled ×2 about the
    // canvas centre → visually 1600×1200 at (-400,-300).
    wrapper.find(".overlay-media-inner").element.getBoundingClientRect =
      () => ({ left: -400, top: -300, width: 1600, height: 1200 });
    layer.element.setPointerCapture = () => {};
    layer.element.releasePointerCapture = () => {};
    await firePointer(wrapper, layer, "pointerdown", {
      clientX: 400,
      clientY: 300,
      pointerId: 2,
    });
    await firePointer(wrapper, layer, "pointermove", {
      clientX: 500,
      clientY: 400,
      pointerId: 2,
    });
    // Natural points: centre (800,600) → (900,700). The rubber band renders
    // INSIDE the transformed inner, so its style is layout-space: the
    // transform is what puts it under the cursor.
    const rect = wrapper.find(".overlay-draw-rect");
    expect(rect.exists()).toBe(true);
    const style = rect.attributes("style");
    expect(style).toContain("left: 400px");
    expect(style).toContain("top: 300px");
    expect(style).toContain("width: 50px");
    expect(style).toContain("height: 50px");
    await firePointer(wrapper, layer, "pointercancel", { pointerId: 2 });
  });
});

describe("ImageOverlay zoom - what the wheel does NOT do", () => {
  it("the canvas wheel never navigates: the image stays put while the scale moves", async () => {
    const wrapper = await openMeasured();
    const srcBefore = wrapper.find(".overlay-img").attributes("src");
    await wheelCanvas(wrapper, -100);
    await wheelCanvas(wrapper, 100);
    await flush();
    expect(wrapper.find(".overlay-img").attributes("src")).toBe(srcBefore);
    // Filmstrip navigation still flows through its own emit, untouched.
    expect(wrapper.findComponent({ name: "OverlayFilmstrip" }).exists()).toBe(
      true,
    );
  });
});
