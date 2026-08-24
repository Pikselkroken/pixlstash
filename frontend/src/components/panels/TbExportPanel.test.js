import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("../../api/folders", () => ({
  browseFilesystem: vi.fn().mockResolvedValue({ path: "/home/me", entries: [] }),
  createFilesystemFolder: vi.fn(),
}));

import TbExportPanel from "./TbExportPanel.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import { browseFilesystem } from "../../api/folders";
import { useExportStore } from "../../stores/useExportStore";

function mountPanel() {
  return mount(TbExportPanel, {
    attachTo: document.body,
    global: { stubs: { "v-icon": true, "v-checkbox": true } },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  browseFilesystem.mockClear();
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("TbExportPanel export-to-folder (#291)", () => {
  it("opens the folder picker without emitting confirm-export", async () => {
    const wrapper = mountPanel();

    await wrapper.get(".tb-export-folder-btn").trigger("click");
    await flushPromises();

    expect(wrapper.findComponent(FolderBrowser).props("open")).toBe(true);
    expect(browseFilesystem).toHaveBeenCalled();
    expect(wrapper.emitted("confirm-export")).toBeUndefined();

    wrapper.unmount();
  });

  it("emits confirm-export-folder with the chosen path and closes both menus", async () => {
    const exportStore = useExportStore();
    exportStore.exportMenuOpen = true;
    const wrapper = mountPanel();

    await wrapper.get(".tb-export-folder-btn").trigger("click");
    await flushPromises();

    await wrapper
      .findComponent(FolderBrowser)
      .vm.$emit("select", "/home/me/exports");
    await flushPromises();

    expect(wrapper.emitted("confirm-export-folder")).toEqual([
      ["/home/me/exports"],
    ]);
    expect(wrapper.findComponent(FolderBrowser).props("open")).toBe(false);
    expect(exportStore.exportMenuOpen).toBe(false);

    wrapper.unmount();
  });

  it("the plain Export button still emits confirm-export, not confirm-export-folder", async () => {
    const wrapper = mountPanel();

    await wrapper.get(".tbm-action--primary").trigger("click");
    await flushPromises();

    expect(wrapper.emitted("confirm-export")).toHaveLength(1);
    expect(wrapper.emitted("confirm-export-folder")).toBeUndefined();

    wrapper.unmount();
  });
});
