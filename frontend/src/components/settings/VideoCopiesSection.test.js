// Settings › Libraries & imports › Video copies (#2807).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VTooltip: { name: "VTooltip", template: "<span />" },
}));

vi.mock("../../api/libraries", () => ({
  fetchVideoCopiesSummary: vi.fn(),
  deleteVideoCopies: vi.fn(),
}));

import VideoCopiesSection from "./VideoCopiesSection.vue";
import { deleteVideoCopies, fetchVideoCopiesSummary } from "../../api/libraries";

async function mountSection() {
  const wrapper = mount(VideoCopiesSection, {
    props: { open: true },
    attachTo: document.body,
  });
  await flushPromises();
  return wrapper;
}

const button = (w) => w.findAll("button").find((b) => b.text().includes("Delete copies"));

beforeEach(() => {
  setActivePinia(createPinia());
  fetchVideoCopiesSummary.mockReset();
  deleteVideoCopies.mockReset();
});

describe("VideoCopiesSection", () => {
  it("shows the count, the size and what the copies are", async () => {
    fetchVideoCopiesSummary.mockResolvedValue({ count: 7, bytes: 1.4 * 1024 ** 3 });
    const w = await mountSection();
    expect(w.text()).toContain("Video copies");
    expect(w.find(".vc-total").text().replace(/\s+/g, " ")).toBe("7 copies · 1.4 GB");
    expect(w.text()).toContain("A deleted copy is made again when its clip is next opened.");
    w.unmount();
  });

  it("is not there when the library has no copies, or they cannot be read", async () => {
    fetchVideoCopiesSummary.mockResolvedValue({ count: 0, bytes: 0 });
    const none = await mountSection();
    expect(none.find(".vc-line").exists()).toBe(false);
    none.unmount();
    fetchVideoCopiesSummary.mockRejectedValue(new Error("offline"));
    const unread = await mountSection();
    expect(unread.find(".vc-line").exists()).toBe(false);
    unread.unmount();
  });

  it("deletes without asking, then reads the line again", async () => {
    fetchVideoCopiesSummary
      .mockResolvedValueOnce({ count: 7, bytes: 1000 })
      .mockResolvedValueOnce({ count: 2, bytes: 500 });
    deleteVideoCopies.mockResolvedValue({ deleted: 5, bytes: 500 });
    const w = await mountSection();
    await button(w).trigger("click");
    await flushPromises();
    expect(deleteVideoCopies).toHaveBeenCalledTimes(1);
    expect(fetchVideoCopiesSummary).toHaveBeenCalledTimes(2);
    expect(w.find(".vc-total").text().replace(/\s+/g, " ")).toBe("2 copies · 500 B");
    w.unmount();
  });

  it("goes with its last copy and leaves focus on the pane's rail entry", async () => {
    const nav = document.createElement("button");
    nav.id = "settings-nav-libraries";
    document.body.appendChild(nav);
    fetchVideoCopiesSummary
      .mockResolvedValueOnce({ count: 7, bytes: 1000 })
      .mockResolvedValueOnce({ count: 0, bytes: 0 });
    deleteVideoCopies.mockResolvedValue({ deleted: 7, bytes: 1000 });
    const w = await mountSection();
    button(w).element.focus();
    await button(w).trigger("click");
    await flushPromises();
    expect(w.find(".vc-line").exists()).toBe(false);
    expect(document.activeElement).toBe(nav);
    w.unmount();
    nav.remove();
  });
});
