// A clip the browser refuses gets a playable copy, and a clip with sound its
// waveform under the frames (#2807).

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import Lightbox from "./Lightbox.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import {
  fetchVideoCopyStatus,
  fetchWaveform,
  requestVideoCopy,
} from "../../api/pictures";

enableAutoUnmount(afterEach);

vi.mock("../../utils/apiClient", async () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn(async (url) => {
      if (String(url).includes("/recipe")) {
        return { data: { available: false, reason: "no_prompt_chunk" } };
      }
      if (String(url).includes("/workflow")) {
        const e = new Error("no workflow");
        e.response = { status: 404 };
        throw e;
      }
      return { data: [] };
    }),
    post: vi.fn(),
    delete: vi.fn(),
  },
  appendShareToken: (u) => u,
  isReadOnly: (await import("vue")).ref(false),
}));

vi.mock("../../api/pictures", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchWaveform: vi.fn(),
  fetchVideoCopyStatus: vi.fn(),
  requestVideoCopy: vi.fn(),
}));

const STUBS = {
  RailTags: true,
  LightboxFilmstrip: {
    name: "LightboxFilmstrip",
    template:
      "<div class='film-stub'><slot name='lead' /><slot name='clip' /></div>",
    methods: { reveal() {} },
  },
  LightboxDescriptionPanel: true,
  LightboxRecipePanel: true,
  LightboxEditPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  PluginParametersUI: true,
  VTooltip: true,
};

const PICTURES = [
  { id: 7, tags: [], format: "png" },
  { id: 9, tags: [], format: "mov", thumbnail: "/pictures/thumbnails/9.webp?v=a1" },
];

const PREPARING = {
  state: "preparing",
  format: "ProRes",
  estimate_seconds: 3,
  remaining_seconds: 2,
  duration_seconds: 6,
};

const settle = async () => {
  await flushPromises();
  await flushPromises();
};

async function openOn(id, { attachTo } = {}) {
  const wrapper = mount(Lightbox, {
    attachTo,
    props: {
      open: false,
      initialImageId: id,
      allImages: PICTURES,
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
    },
    global: { stubs: STUBS },
  });
  await wrapper.setProps({ open: true });
  await settle();
  return wrapper;
}

/** The browser says it cannot play what the <video> holds. */
async function refuse(wrapper, code = 4) {
  const video = wrapper.find("video").element;
  Object.defineProperty(video, "error", { value: { code, message: "nope" } });
  video.dispatchEvent(new Event("error"));
  await settle();
}

const box = (w) => w.find(".lightbox-video-error");
const hint = (w) => w.find(".lightbox-copy-hint");
const board = (w) => w.findComponent({ name: "VideoStoryboard" });

beforeEach(() => {
  setActivePinia(createPinia());
  useSidebarStore().lightboxRailOpen = true;
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  fetchWaveform.mockReset().mockResolvedValue({ duration: null, peaks: null });
  fetchVideoCopyStatus.mockReset();
  requestVideoCopy.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a playable copy of a clip the browser refuses", () => {
  it("asks for one, unconfirmed, the moment the browser refuses the file", async () => {
    requestVideoCopy.mockResolvedValue(PREPARING);
    const w = await openOn(9);
    expect(w.find("video").attributes("src")).toBe("http://test/pictures/9.mov");
    await refuse(w);
    expect(requestVideoCopy).toHaveBeenCalledWith(9, { confirm: false });
  });

  it("asks for nothing when the failure is not 'format not supported'", async () => {
    const w = await openOn(9);
    await refuse(w, 2);
    expect(requestVideoCopy).not.toHaveBeenCalled();
    expect(box(w).text()).toContain("Your browser cannot play this video format (MOV).");
  });

  it("plays a ready copy in place of the original", async () => {
    requestVideoCopy.mockResolvedValue({ state: "ready" });
    const w = await openOn(9);
    await refuse(w);
    expect(box(w).exists()).toBe(false);
    expect(w.find("video").attributes("src")).toBe("/api/v1/pictures/video-copies/9.mp4");
  });

  it("plays an existing copy for a share token that may not start one", async () => {
    requestVideoCopy.mockRejectedValue({ response: { status: 403 } });
    fetchVideoCopyStatus.mockResolvedValue({ state: "ready" });
    const w = await openOn(9);
    await refuse(w);
    expect(fetchVideoCopyStatus).toHaveBeenCalledWith(9);
    expect(w.find("video").attributes("src")).toBe("/api/v1/pictures/video-copies/9.mp4");
  });

  it("does not ask again when the copy will not play either", async () => {
    requestVideoCopy.mockResolvedValue({ state: "ready" });
    const w = await openOn(9);
    await refuse(w);
    await refuse(w);
    expect(requestVideoCopy).toHaveBeenCalledTimes(1);
    expect(box(w).text()).toContain("Your browser cannot play this video format");
  });

  it("says a short copy is being prepared, and polls until it is ready", async () => {
    requestVideoCopy.mockResolvedValue(PREPARING);
    fetchVideoCopyStatus.mockResolvedValueOnce(PREPARING).mockResolvedValueOnce({ state: "ready" });
    const w = await openOn(9);
    await refuse(w);
    expect(box(w).text()).toContain("Preparing a playable copy");
    expect(box(w).text()).toContain(
      "About 2 seconds. This browser can't play ProRes, so PixlStash makes an MP4 copy once and keeps it.",
    );
    expect(hint(w).text()).toBe("Frames seek once the copy plays");
    // The strip still shows the original's frames meanwhile.
    expect(board(w).exists()).toBe(true);

    await vi.advanceTimersByTimeAsync(1999);
    expect(fetchVideoCopyStatus).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await settle();
    expect(fetchVideoCopyStatus).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(fetchVideoCopyStatus).toHaveBeenCalledTimes(2);
    expect(w.find("video").attributes("src")).toBe("/api/v1/pictures/video-copies/9.mp4");
    expect(hint(w).exists()).toBe(false);
  });

  it("offers a long copy, and makes it only when asked", async () => {
    requestVideoCopy
      .mockResolvedValueOnce({
        state: "offered",
        format: "ProRes",
        estimate_seconds: 118,
        duration_seconds: 252,
      })
      .mockResolvedValueOnce({
        ...PREPARING,
        estimate_seconds: 118,
        remaining_seconds: 100,
      });
    const w = await openOn(9);
    await refuse(w);
    expect(box(w).text()).toContain("This browser can't play ProRes");
    expect(box(w).text()).toContain(
      "A playable copy of this 4 min 12 s clip takes about 2 minutes to make. It is made once and kept.",
    );
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchVideoCopyStatus).not.toHaveBeenCalled();

    const make = box(w).findAll("button").find((b) => b.text().includes("Make a playable copy"));
    await make.trigger("click");
    await settle();
    expect(requestVideoCopy).toHaveBeenLastCalledWith(9, { confirm: true });
    expect(box(w).text()).toContain("Making a playable copy");
    expect(box(w).text()).toContain("About 1 min 40 s left.");
    fetchVideoCopyStatus.mockResolvedValue({ state: "ready" });
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchVideoCopyStatus).toHaveBeenCalledTimes(1);
  });

  it("keeps the keyboard in the box, then on the stage once the clip plays", async () => {
    requestVideoCopy
      .mockResolvedValueOnce({ state: "offered", format: "ProRes", estimate_seconds: 60 })
      .mockResolvedValueOnce({ ...PREPARING, estimate_seconds: 60 });
    fetchVideoCopyStatus.mockResolvedValue({ state: "ready" });
    const w = await openOn(9, { attachTo: document.body });
    await refuse(w);
    const make = box(w).findAll("button").find((b) => b.text().includes("Make a playable copy"));
    make.element.focus();
    await make.trigger("click");
    await settle();
    expect(document.activeElement).toBe(w.find(".lightbox-video-download-btn").element);
    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(w.find("video").exists()).toBe(true);
    expect(document.activeElement).toBe(w.find(".lightbox-canvas").element);
  });

  it("names 'this format' when the server cannot", async () => {
    requestVideoCopy.mockResolvedValue({ ...PREPARING, format: null });
    const w = await openOn(9);
    await refuse(w);
    expect(box(w).text()).toContain("This browser can't play this format, so");
  });

  it("stops polling when the lightbox steps to another picture", async () => {
    requestVideoCopy.mockResolvedValue(PREPARING);
    fetchVideoCopyStatus.mockResolvedValue(PREPARING);
    const w = await openOn(9);
    await refuse(w);
    await w.setProps({ initialImageId: 7 });
    await settle();
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchVideoCopyStatus).not.toHaveBeenCalled();
  });

  it("stops polling when the lightbox goes away", async () => {
    requestVideoCopy.mockResolvedValue(PREPARING);
    const w = await openOn(9);
    await refuse(w);
    w.unmount();
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchVideoCopyStatus).not.toHaveBeenCalled();
  });

  it("ends at the plain box when the copy fails, and asks no more", async () => {
    requestVideoCopy.mockResolvedValue(PREPARING);
    fetchVideoCopyStatus.mockResolvedValue({
      state: "failed",
      detail: "The copy could not be made.",
    });
    const w = await openOn(9);
    await refuse(w);
    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(box(w).text()).toContain("Your browser cannot play this video format (MOV).");
    expect(box(w).text()).toContain("The copy could not be made.");
    expect(hint(w).exists()).toBe(false);
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchVideoCopyStatus).toHaveBeenCalledTimes(1);
    expect(requestVideoCopy).toHaveBeenCalledTimes(1);
  });
});

describe("the waveform under the frames", () => {
  it("hands the strip the clip's peaks", async () => {
    const wave = { duration: 5, peaks: [0.1, 0.9] };
    fetchWaveform.mockResolvedValue(wave);
    const w = await openOn(9);
    expect(fetchWaveform).toHaveBeenCalledWith(9);
    expect(board(w).props("waveform")).toEqual(wave);
  });

  it("hands it nothing when the server has no waveform to give", async () => {
    const err = new Error("no ffmpeg");
    err.response = { status: 503 };
    fetchWaveform.mockRejectedValue(err);
    const w = await openOn(9);
    expect(board(w).props("waveform")).toBe(null);
  });

  it("does not ask on a picture", async () => {
    await openOn(7);
    expect(fetchWaveform).not.toHaveBeenCalled();
  });
});
