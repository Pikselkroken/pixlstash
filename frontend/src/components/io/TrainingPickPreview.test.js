// One pick at full size: the thumbnail until the metadata names the file.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({ getPictureMetadata: vi.fn() }));
vi.mock("../../api/pictures", () => ({
  getPictureMetadata: api.getPictureMetadata,
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
  pictureMediaUrl: (id, format) => `/pictures/${id}.${format.toLowerCase()}`,
}));

import TrainingPickPreview from "./TrainingPickPreview.vue";

function mountPreview() {
  return mount(TrainingPickPreview, {
    props: { pick: null },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  });
}

const PICK = { picture_id: 9, rank: 3, label: "portrait · smile", off: false };

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TrainingPickPreview", () => {
  it("shows the thumbnail, then the picture's own file", async () => {
    let answer;
    api.getPictureMetadata.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    const wrapper = mountPreview();
    await wrapper.setProps({ pick: PICK });
    const media = () => wrapper.find('[data-testid="tpp-media"]');
    expect(media().attributes("src")).toBe("/thumb/9.webp");
    answer({ format: "PNG" });
    await flushPromises();
    expect(media().element.tagName).toBe("IMG");
    expect(media().attributes("src")).toBe("/pictures/9.png");

    // A file the browser cannot draw falls back to the thumbnail.
    await media().trigger("error");
    expect(media().attributes("src")).toBe("/thumb/9.webp");
    wrapper.unmount();
  });

  it("plays a clip and keeps the thumbnail when the metadata fails", async () => {
    api.getPictureMetadata.mockResolvedValueOnce({ format: "mp4" });
    const wrapper = mountPreview();
    await wrapper.setProps({ pick: PICK });
    await flushPromises();
    expect(wrapper.find('[data-testid="tpp-media"]').element.tagName).toBe(
      "VIDEO",
    );

    api.getPictureMetadata.mockRejectedValueOnce(new Error("gone"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await wrapper.setProps({ pick: { ...PICK, picture_id: 10 } });
    await flushPromises();
    expect(wrapper.find('[data-testid="tpp-media"]').attributes("src")).toBe(
      "/thumb/10.webp",
    );
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
    wrapper.unmount();
  });

  it("zooms on a click and asks to leave the pick out", async () => {
    api.getPictureMetadata.mockResolvedValue({ format: "png" });
    const wrapper = mountPreview();
    await wrapper.setProps({ pick: PICK });
    await flushPromises();
    const stage = wrapper.find('[data-testid="tpp-stage"]');
    expect(stage.classes()).not.toContain("tpp-stage--zoomed");
    await wrapper.find('[data-testid="tpp-media"]').trigger("click");
    expect(stage.classes()).toContain("tpp-stage--zoomed");

    const toggle = wrapper.find('[data-testid="tpp-toggle"]');
    expect(toggle.text()).toContain("Leave out of the set");
    await toggle.trigger("click");
    expect(wrapper.emitted("toggle")).toEqual([[9]]);
    await wrapper.setProps({ pick: { ...PICK, off: true } });
    expect(toggle.text()).toContain("Put back in the set");
    await wrapper.setProps({ locked: true });
    expect(toggle.attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });
});
