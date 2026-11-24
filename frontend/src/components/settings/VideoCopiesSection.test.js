// The "Video copies" section under Import folders (#2807): shown only when
// the open library has copies, and Delete copies clears them after a confirm.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

const api = vi.hoisted(() => ({
  getVideoCopies: vi.fn(),
  deleteVideoCopies: vi.fn(),
}));
vi.mock("../../api/libraries", () => api);
vi.mock("../widgets/AppDialog.vue", () => ({
  default: {
    name: "AppDialog",
    props: ["open", "title"],
    template:
      "<div v-if='open' class='dialog-stub'><slot /><slot name='footer' /></div>",
  },
}));

import VideoCopiesSection from "./VideoCopiesSection.vue";

function mountSection() {
  return mount(VideoCopiesSection, { props: { open: true } });
}

beforeEach(() => {
  api.getVideoCopies.mockReset();
  api.deleteVideoCopies.mockReset();
});

describe("the Video copies section", () => {
  it("is not shown when the library has no copies", async () => {
    api.getVideoCopies.mockResolvedValue({ count: 0, bytes: 0 });
    const w = mountSection();
    await flushPromises();
    expect(api.getVideoCopies).toHaveBeenCalledTimes(1);
    expect(w.find("#vcs-title").exists()).toBe(false);
  });

  it("is shown with the count and the size when there are copies", async () => {
    api.getVideoCopies.mockResolvedValue({ count: 2, bytes: 1572864 });
    const w = mountSection();
    await flushPromises();
    expect(w.find("#vcs-title").text()).toBe("Video copies");
    expect(w.find(".vcs-line").text()).toBe("2 copies · 1.5 MB");
    expect(w.find(".vcs-delete").text()).toBe("Delete copies");
  });

  it("deletes the copies after the confirm, and the section goes", async () => {
    api.getVideoCopies.mockResolvedValue({ count: 1, bytes: 2048 });
    api.deleteVideoCopies.mockResolvedValue({ removed: 1, bytes: 2048 });
    const w = mountSection();
    await flushPromises();
    await w.find(".vcs-delete").trigger("click");
    expect(api.deleteVideoCopies).not.toHaveBeenCalled();
    const confirm = w.findAll("button").find((b) => b.text() === "Delete copies" && b.element.closest(".dialog-stub"));
    await confirm.trigger("click");
    await flushPromises();
    expect(api.deleteVideoCopies).toHaveBeenCalledTimes(1);
    expect(w.find("#vcs-title").exists()).toBe(false);
  });

  it("says so in the dialog when the delete fails", async () => {
    api.getVideoCopies.mockResolvedValue({ count: 1, bytes: 2048 });
    api.deleteVideoCopies.mockRejectedValue(new Error("boom"));
    const w = mountSection();
    await flushPromises();
    await w.find(".vcs-delete").trigger("click");
    const confirm = w.findAll("button").find((b) => b.text() === "Delete copies" && b.element.closest(".dialog-stub"));
    await confirm.trigger("click");
    await flushPromises();
    expect(w.find(".vcs-error").text()).toBe("The copies could not be deleted. Try again.");
    expect(w.find("#vcs-title").exists()).toBe(true);
  });
});
