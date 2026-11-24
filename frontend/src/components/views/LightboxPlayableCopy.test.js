// A video the browser refuses (MEDIA_ERR_SRC_NOT_SUPPORTED) gets a playable
// copy (#2807): the status decides between waiting, an offer, the copy, and
// the error box.

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const proxy = vi.hoisted(() => ({
  fetchProxyStatus: vi.fn(),
  startProxy: vi.fn(),
  fetchWaveform: vi.fn(),
}));
vi.mock("../../api/pictures", async (importOriginal) => ({
  ...(await importOriginal()),
  ...proxy,
}));

vi.mock("../../utils/apiClient", async () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn(async () => ({ data: [] })),
    post: vi.fn(),
    delete: vi.fn(),
  },
  appendShareToken: (u) => u,
  isReadOnly: (await import("vue")).ref(false),
}));

import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import Lightbox from "./Lightbox.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";

enableAutoUnmount(afterEach);

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

// Fake timers drive the 2 s poll; this lets queued promises settle.
const flush = async () => {
  for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0);
};

async function openOn(id) {
  const wrapper = mount(Lightbox, {
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
  await flush();
  return wrapper;
}

async function stepTo(wrapper, id) {
  await wrapper.setProps({ initialImageId: id });
  await flush();
}

// jsdom never fails a media load, so the browser's refusal is staged: the
// element's error is a code-4 MediaError, then the error event is fired.
async function refuseVideo(wrapper, code = 4) {
  const video = wrapper.find("video");
  Object.defineProperty(video.element, "error", {
    configurable: true,
    value: { code, message: "not supported" },
  });
  await video.trigger("error");
  await flush();
}

const statusOf = (state, extra = {}) => ({
  state,
  estimate_seconds: 3,
  elapsed_seconds: 0,
  format: "ProRes",
  ...extra,
});

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  useSidebarStore().lightboxRailOpen = true;
  proxy.fetchProxyStatus.mockReset();
  proxy.startProxy.mockReset();
  proxy.fetchWaveform.mockReset().mockResolvedValue(null);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("a playable copy of a refused video", () => {
  it("asks for the copy's status when the browser refuses the original", async () => {
    proxy.fetchProxyStatus.mockResolvedValue(statusOf("preparing"));
    const w = await openOn(9);
    await refuseVideo(w);
    expect(proxy.fetchProxyStatus).toHaveBeenCalledWith(9);
    expect(w.find(".lightbox-proxy-title").text()).toBe("Preparing a playable copy");
  });

  it("polls every 2 s while preparing, and swaps the source once ready", async () => {
    proxy.fetchProxyStatus
      .mockResolvedValueOnce(statusOf("preparing"))
      .mockResolvedValueOnce(statusOf("preparing"))
      .mockResolvedValueOnce(statusOf("ready"));
    const w = await openOn(9);
    await refuseVideo(w);
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    expect(proxy.fetchProxyStatus).toHaveBeenCalledTimes(3);
    expect(w.find("video").attributes("src")).toBe("/api/v1/pictures/proxies/9.mp4");
    expect(w.find(".lightbox-video-error").exists()).toBe(false);
  });

  it("stops polling when the reader steps to another picture", async () => {
    proxy.fetchProxyStatus.mockResolvedValue(statusOf("preparing"));
    const w = await openOn(9);
    await refuseVideo(w);
    await stepTo(w, 7);
    const calls = proxy.fetchProxyStatus.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(proxy.fetchProxyStatus.mock.calls.length).toBe(calls);
  });

  it("does not ask again when the copy itself fails to play", async () => {
    proxy.fetchProxyStatus.mockResolvedValue(statusOf("ready"));
    const w = await openOn(9);
    await refuseVideo(w);
    expect(w.find("video").attributes("src")).toBe("/api/v1/pictures/proxies/9.mp4");
    await refuseVideo(w);
    expect(proxy.fetchProxyStatus).toHaveBeenCalledTimes(1);
    expect(w.find(".lightbox-video-error-msg").text()).toContain("cannot play this video format");
  });

  it("offers a long copy, and Make starts it", async () => {
    proxy.fetchProxyStatus.mockResolvedValue(
      statusOf("offered", { estimate_seconds: 120, duration_seconds: 252 }),
    );
    proxy.startProxy.mockResolvedValue(statusOf("preparing", { estimate_seconds: 120 }));
    const w = await openOn(9);
    await refuseVideo(w);
    const make = w.findAll("button").find((b) => b.text().includes("Make a playable copy"));
    expect(make).toBeTruthy();
    expect(w.text()).toContain(
      "A playable copy of this 4 min 12 s clip takes about 2 minutes to make.",
    );
    await make.trigger("click");
    await flush();
    expect(proxy.startProxy).toHaveBeenCalledWith(9);
    expect(w.find(".lightbox-proxy-title").text()).toBe("Making a playable copy");
  });

  it("shows the error box for a failed copy, and does not poll", async () => {
    proxy.fetchProxyStatus.mockResolvedValue(statusOf("failed"));
    const w = await openOn(9);
    await refuseVideo(w);
    expect(w.find(".lightbox-video-error-msg").text()).toContain("cannot play this video format");
    await vi.advanceTimersByTimeAsync(10000);
    expect(proxy.fetchProxyStatus).toHaveBeenCalledTimes(1);
  });

  it("retries a status request that fails, and still plays the copy", async () => {
    proxy.fetchProxyStatus
      .mockResolvedValueOnce(statusOf("preparing"))
      .mockRejectedValueOnce(new Error("blip"))
      .mockResolvedValueOnce(statusOf("ready"));
    const w = await openOn(9);
    await refuseVideo(w);
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    expect(w.find(".lightbox-proxy-title").text()).toBe("Preparing a playable copy");
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    expect(proxy.fetchProxyStatus).toHaveBeenCalledTimes(3);
    expect(w.find("video").attributes("src")).toBe("/api/v1/pictures/proxies/9.mp4");
  });

  it("gives up with the error box after three failed status requests", async () => {
    proxy.fetchProxyStatus.mockRejectedValue(new Error("down"));
    const w = await openOn(9);
    await refuseVideo(w);
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    expect(proxy.fetchProxyStatus).toHaveBeenCalledTimes(3);
    expect(w.find(".lightbox-video-error-msg").text()).toContain("cannot play this video format");
    await vi.advanceTimersByTimeAsync(10000);
    expect(proxy.fetchProxyStatus).toHaveBeenCalledTimes(3);
  });

  it("says nothing about time when the server has no estimate", async () => {
    proxy.fetchProxyStatus.mockResolvedValue(
      statusOf("preparing", { estimate_seconds: null }),
    );
    const w = await openOn(9);
    await refuseVideo(w);
    const text = w.find(".lightbox-video-error").text();
    expect(text).toContain("This browser can't play ProRes");
    expect(text).not.toContain("About");
  });
});
