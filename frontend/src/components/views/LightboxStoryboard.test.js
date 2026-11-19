// On a video, the lightbox's filmstrip band shows the clip's frames, one
// switch away from the set's pictures (#2147).

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import Lightbox from "./Lightbox.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";

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

const STUBS = {
  RailTags: true,
  // Thin, so the band's two slots render where the real band puts them.
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

const flush = () => new Promise((r) => setTimeout(r, 0));

const PICTURES = [
  { id: 7, tags: [], format: "png" },
  { id: 9, tags: [], format: "mp4", thumbnail: "/pictures/thumbnails/9.webp?v=a1" },
];

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
  await flush();
  return wrapper;
}

async function stepTo(wrapper, id) {
  await wrapper.setProps({ initialImageId: id });
  await flush();
  await flush();
}

const board = (w) => w.findComponent({ name: "VideoStoryboard" });
const bandSwitch = (w) => w.find('[role="radiogroup"][aria-label="Band shows"]');

beforeEach(() => {
  setActivePinia(createPinia());
  useSidebarStore().lightboxRailOpen = true;
});

describe("the clip in the filmstrip band", () => {
  it("shows a video's frames, from its storyboard at the thumbnail's version", async () => {
    const w = await openOn(9);
    expect(bandSwitch(w).exists()).toBe(true);
    expect(board(w).props("src")).toBe("/api/v1/pictures/storyboards/9.webp?v=a1");
  });

  it("shows nothing of the kind on a picture", async () => {
    const w = await openOn(7);
    expect(bandSwitch(w).exists()).toBe(false);
    expect(board(w).exists()).toBe(false);
  });

  it("switches back to the pictures, and the choice holds across the set", async () => {
    const w = await openOn(9);
    await bandSwitch(w).findAll('[role="radio"]')[0].trigger("click");
    expect(board(w).exists()).toBe(false);
    expect(bandSwitch(w).exists()).toBe(true);
    await stepTo(w, 7);
    await stepTo(w, 9);
    expect(board(w).exists()).toBe(false);
  });

  it("keeps the filmstrip, with no switch, for a clip with no storyboard", async () => {
    const w = await openOn(9);
    board(w).vm.$emit("unavailable");
    await flush();
    expect(board(w).exists()).toBe(false);
    expect(bandSwitch(w).exists()).toBe(false);
  });

  it("hands the strip the lightbox's own video to follow and seek", async () => {
    const w = await openOn(9);
    expect(board(w).props("media")).toBe(w.find("video").element);
  });
});
