import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const api = vi.hoisted(() => ({
  listImportFolders: vi.fn(),
  getInbox: vi.fn(),
  markInboxSeen: vi.fn(),
}));
vi.mock("../../api/folders", () => api);

import InboxBar from "./InboxBar.vue";

const BATCHES = [
  {
    import_source_folder: "/home/me/renders",
    label: "ComfyUI output",
    count: 29,
    first_imported_at: "2026-10-27T14:02:00+00:00",
    last_imported_at: "2026-10-27T14:20:00+00:00",
  },
  {
    import_source_folder: "/home/me/phone",
    label: "Phone uploads",
    count: 12,
    first_imported_at: "2026-10-26T18:40:00+00:00",
    last_imported_at: "2026-10-26T18:40:00+00:00",
  },
];

function mountBar(props = {}) {
  return mount(InboxBar, {
    props,
    global: {
      stubs: {
        VMenu: { template: "<div><slot name='activator' :props='{}' /><slot /></div>" },
        VIcon: true,
      },
    },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  api.listImportFolders.mockResolvedValue({
    folders: [
      { id: 1, folder: "/home/me/renders", label: "ComfyUI output" },
      { id: 2, folder: "/home/me/phone", label: "Phone uploads", missing: true },
    ],
  });
  api.getInbox.mockResolvedValue({ total: 41, folder_count: 2, batches: BATCHES });
  api.markInboxSeen.mockResolvedValue({ marked: 29 });
});

describe("InboxBar", () => {
  it("counts what is new and lists one row per batch", async () => {
    const wrapper = mountBar();
    await flushPromises();
    expect(wrapper.find(".inbox-bar__count").text()).toBe(
      "41 new from 2 import folders",
    );
    expect(wrapper.findAll(".inbox-batch__name").map((b) => b.text())).toEqual([
      "ComfyUI output",
      "Phone uploads",
    ]);
    expect(wrapper.text()).toContain("Folder not found");
  });

  it("marks one batch seen by its folder and arrival times", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const markSeen = wrapper
      .findAll(".inbox-batch button")
      .find((b) => b.text() === "Mark seen");
    await markSeen.trigger("click");
    await flushPromises();
    expect(api.markInboxSeen).toHaveBeenCalledWith({
      import_source_folder: "/home/me/renders",
      since: BATCHES[0].first_imported_at,
      until: BATCHES[0].last_imported_at,
    });
    expect(wrapper.emitted("marked")).toHaveLength(1);
  });

  it("marks everything, or only the folder it is narrowed to", async () => {
    const all = mountBar();
    await flushPromises();
    await all
      .findAll("button")
      .find((b) => b.text() === "Mark all seen")
      .trigger("click");
    expect(api.markInboxSeen).toHaveBeenLastCalledWith({ all: true });

    const narrowed = mountBar({ folder: "/home/me/phone" });
    await flushPromises();
    expect(narrowed.find(".inbox-bar__count").text()).toBe("12 new");
    expect(narrowed.findAll(".inbox-batch")).toHaveLength(1);
    await narrowed
      .findAll("button")
      .find((b) => b.text() === "Mark folder seen")
      .trigger("click");
    expect(api.markInboxSeen).toHaveBeenLastCalledWith({
      import_source_folder: "/home/me/phone",
    });
  });

  it("sends folder changes to Settings rather than editing them here", async () => {
    const wrapper = mountBar();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Manage import folders"))
      .trigger("click");
    expect(wrapper.emitted("manage")).toHaveLength(1);
  });
});
