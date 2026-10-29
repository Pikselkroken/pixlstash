// The lightbox's head (`pinnable`): the faces stand beside the title and
// stars, and Edit faces is part of the face block at every count.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("../../api/pictures", () => ({
  openPictureLocation: vi.fn(),
  pictureThumbnailUrl: (id) => `/thumbs/${id}.webp`,
}));

import RailPicture from "./RailPicture.vue";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { sessionContext } from "../../utils/apiClient";

enableAutoUnmount(afterEach);

beforeEach(() => {
  setActivePinia(createPinia());
  sessionContext.value = null;
});

const STUBS = {
  "v-icon": true,
  Tooltip: { props: ["text"], template: '<span class="tip" :data-text="text" />' },
  StarRatingOverlay: true,
  RailTags: true,
  LightboxDescriptionPanel: true,
  FacesPanel: true,
};

const facesOf = (n) =>
  Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    bbox: [i * 10, 0, i * 10 + 50, 50],
    character_id: i === 0 ? 3 : null,
  }));

function mountHead(count, props = {}) {
  useEntityNamesStore().characterNames = { 3: "Ranger" };
  return mount(RailPicture, {
    props: {
      pictureId: 7,
      card: { id: 7, width: 800, height: 600 },
      title: "Three riders resting beside their bikes",
      pinnable: true,
      onDark: true,
      faces: facesOf(count),
      ...props,
    },
    global: { stubs: STUBS },
  });
}

const crops = (w) => w.findAll(".rail-face:not(:has(.rail-face__more))");

describe("the lightbox head", () => {
  it("no faces: the title takes the width and 'No faces' edits", () => {
    const w = mountHead(0);
    expect(w.find(".rail-id").classes()).toContain("rail-id--faceless");
    expect(w.find(".rail-id__thumb").exists()).toBe(false);
    const edit = w.find(".rail-face-edit");
    expect(edit.text()).toBe("No faces");
    expect(edit.attributes("aria-label")).toBe("No faces: edit faces");
  });

  it("one face: a 64 px portrait with its name on the edit button", () => {
    const w = mountHead(1);
    expect(crops(w)).toHaveLength(1);
    expect(w.find(".rail-face__crop").attributes("style")).toContain(
      "width: 64px",
    );
    expect(w.find(".rail-face__name").exists()).toBe(false);
    expect(w.find(".rail-picture__title").classes()).toContain(
      "rail-picture__title--tall",
    );
    const edit = w.find(".rail-face-edit");
    expect(edit.text()).toBe("Ranger");
    expect(edit.find(".rail-face-edit__text--named").exists()).toBe(true);
    expect(edit.attributes("aria-label")).toBe("Ranger: edit faces");
  });

  it("five faces all show, nameless, with the pencil as the last cell", () => {
    const w = mountHead(5);
    expect(crops(w)).toHaveLength(5);
    expect(w.find(".rail-face__more").exists()).toBe(false);
    expect(w.find(".rail-face__name").exists()).toBe(false);
    expect(w.find(".rail-face__crop").attributes("style")).toContain(
      "width: 32px",
    );
    const cells = w.findAll(".rail-faces > *");
    const last = cells[cells.length - 1];
    expect(last.classes()).toContain("rail-face-edit--cell");
    expect(last.attributes("aria-label")).toBe("Edit faces");
    expect(w.findAll(".rail-faces .tip")[0].attributes("data-text")).toBe(
      "Ranger",
    );
  });

  it("six or more show four and a +N", () => {
    const w = mountHead(7);
    expect(crops(w)).toHaveLength(4);
    expect(w.find(".rail-face__more").text()).toBe("+3");
  });

  it("a read-only session keeps the lone face's name, without a button", () => {
    sessionContext.value = { scope: "READ" };
    const w = mountHead(1);
    const name = w.find(".rail-face-edit");
    expect(name.element.tagName).toBe("SPAN");
    expect(name.text()).toBe("Ranger");
  });
});
