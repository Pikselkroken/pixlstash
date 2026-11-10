// One picture's Properties, in the grid rail and the lightbox's Info alike:
// the id first (#1379), then what the file is, frames only where they say
// something, and what has it locked.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

// Properties starts folded; these tests read its rows, so the remembered
// state opens it before the component's module reads it.
vi.hoisted(() => {
  window.localStorage.setItem(
    "pixlstash:collapsedSections",
    JSON.stringify({ "lightbox:properties": false }),
  );
});

vi.mock("../../api/pictures", () => ({
  openPictureLocation: vi.fn(),
  pictureThumbnailUrl: (id) => `/thumbnails/${id}.webp`,
}));
vi.mock("../../api/characters", () => ({
  addCharacterFacesByFaceId: vi.fn(),
  removeCharacterFacesByFaceId: vi.fn(),
}));

import RailPicture from "./RailPicture.vue";

const STUBS = {
  RailTags: true,
  LightboxDescriptionPanel: true,
  StarRatingOverlay: true,
  FacesPanel: true,
  Tooltip: true,
  "v-icon": true,
};

function rows(image, props = {}) {
  setActivePinia(createPinia());
  const wrapper = mount(RailPicture, {
    props: { pictureId: image.id ?? 1, image, ...props },
    global: { stubs: STUBS },
  });
  const labels = wrapper.findAll(".rail-picture__props dt").map((d) => d.text());
  const values = wrapper.findAll(".rail-picture__props dd").map((d) => d.text());
  return labels.map((label, i) => ({ label, value: values[i] }));
}

const value = (image, label, props) =>
  rows(image, props).find((r) => r.label === label)?.value;

describe("RailPicture's Properties", () => {
  it("names the picture's id first", () => {
    const list = rows({ id: 4217, width: 10, height: 20, file_path: "/home/me/a.png" });
    expect(list[0]).toEqual({ label: "ID", value: "4217" });
    expect(list.map((r) => r.label)).toEqual(["ID", "File", "Dimensions", "Aspect", "Type"]);
    expect(value({ id: 1, width: 1600, height: 1200 }, "Aspect")).toBe("4:3");
  });

  it("has no id row without an id", () => {
    expect(rows({ width: 10, height: 20 }).map((r) => r.label)).not.toContain("ID");
  });

  it("shows a video's frame count and runtime", () => {
    const video = { id: 1, format: "mp4", frame_count: 1440 };
    expect(value(video, "Frames")).toBe((1440).toLocaleString());
    expect(value(video, "Type")).toBe("Video · MP4");
    expect(value(video, "Runtime", { videoDuration: 3725 })).toBe("1:02:05");
    expect(value(video, "Runtime", { videoDuration: 65 })).toBe("1:05");
  });

  it("shows a one-frame video's count, but not an unknown one", () => {
    expect(value({ id: 1, format: "mp4", frame_count: 1 }, "Frames")).toBe("1");
    expect(value({ id: 1, format: "mp4", frame_count: null }, "Frames")).toBeUndefined();
  });

  it("shows an animated GIF's frame count, and none for a still", () => {
    expect(value({ id: 1, format: "gif", frame_count: 12 }, "Frames")).toBe("12");
    expect(value({ id: 1, format: "png", frame_count: 1 }, "Frames")).toBeUndefined();
    expect(value({ id: 1, format: "gif", frame_count: 1 }, "Frames")).toBeUndefined();
    expect(value({ id: 1, format: "png" }, "Runtime", { videoDuration: 9 })).toBeUndefined();
  });

  it("names the sets locking it, and reads the lightbox's smart score", () => {
    const image = {
      id: 1,
      smartScore: 3.6912,
      locked_by_sets: [{ name: "Keepers" }, { name: "Final" }],
    };
    expect(value(image, "Locked by")).toBe("Keepers, Final");
    expect(value(image, "Quality")).toBe("3.69");
  });
});
