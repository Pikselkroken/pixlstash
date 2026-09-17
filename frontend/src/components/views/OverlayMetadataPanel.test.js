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
});
