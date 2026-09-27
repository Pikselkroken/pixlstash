// The Metadata section names the picture's id (#1379).

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", () => ({ API_BASE_URL: "/api/v1" }));
vi.mock("../../api/pictures", () => ({ openPictureLocation: vi.fn() }));

import OverlayMetadataPanel from "./OverlayMetadataPanel.vue";

const rows = (wrapper) =>
  wrapper.findAll(".metadata-info-item").map((item) => ({
    label: item.find("dt").text(),
    value: item.find(".metadata-info-value").text(),
  }));

describe("OverlayMetadataPanel", () => {
  it("shows the picture id as the first info row", () => {
    const wrapper = mount(OverlayMetadataPanel, {
      props: { image: { id: 4217, width: 10, height: 20 } },
      global: { stubs: { "v-icon": true } },
    });
    expect(rows(wrapper)[0]).toEqual({ label: "ID", value: "4217" });
  });

  it("has no id row without an image id", () => {
    const wrapper = mount(OverlayMetadataPanel, {
      props: { image: { width: 10, height: 20 } },
      global: { stubs: { "v-icon": true } },
    });
    expect(rows(wrapper).map((r) => r.label)).not.toContain("ID");
  });

  const frames = (image) =>
    rows(
      mount(OverlayMetadataPanel, {
        props: { image },
        global: { stubs: { "v-icon": true } },
      }),
    ).find((r) => r.label === "Frames")?.value;

  it("shows a video's frame count", () => {
    expect(frames({ id: 1, format: "mp4", frame_count: 1440 })).toBe(
      (1440).toLocaleString(),
    );
  });

  it("shows a one-frame video's count, but not an unknown one", () => {
    expect(frames({ id: 1, format: "mp4", frame_count: 1 })).toBe("1");
    expect(frames({ id: 1, format: "mp4", frame_count: null })).toBeUndefined();
  });

  it("shows an animated GIF's frame count", () => {
    expect(frames({ id: 1, format: "gif", frame_count: 12 })).toBe("12");
  });

  it("has no frames row for a still image", () => {
    expect(frames({ id: 1, format: "png", frame_count: 1 })).toBeUndefined();
    expect(frames({ id: 1, format: "gif", frame_count: 1 })).toBeUndefined();
  });
});
