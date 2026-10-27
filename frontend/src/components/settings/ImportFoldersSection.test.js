import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const api = vi.hoisted(() => ({
  listImportFolders: vi.fn(),
  getInbox: vi.fn(),
  markInboxSeen: vi.fn(),
  listReferenceFolders: vi.fn(),
  createFolder: vi.fn(),
  patchFolder: vi.fn(),
  deleteFolder: vi.fn(),
}));
vi.mock("../../api/folders", () => api);
vi.mock("../editors/FolderBrowser.vue", () => ({
  default: { name: "FolderBrowser", template: "<div />" },
}));
vi.mock("../widgets/AppDialog.vue", () => ({
  default: {
    name: "AppDialog",
    props: ["open", "title"],
    template:
      "<div v-if='open' class='dialog-stub'><slot /><slot name='footer' /></div>",
  },
}));

import ImportFoldersSection from "./ImportFoldersSection.vue";

const RENDERS = {
  id: 3,
  folder: "/home/me/renders",
  label: "ComfyUI output",
  delete_after_import: false,
  picture_count: 12,
  last_imported_at: null,
  missing: true,
};

function mountSection() {
  return mount(ImportFoldersSection, {
    props: { open: true },
    global: {
      stubs: {
        VMenu: { template: "<div><slot name='activator' :props='{}' /><slot /></div>" },
        VIcon: true,
      },
    },
  });
}

function button(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().trim() === text);
}

beforeEach(() => {
  setActivePinia(createPinia());
  api.listImportFolders.mockResolvedValue({ folders: [RENDERS] });
  api.getInbox.mockResolvedValue({ total: 0, folder_count: 0, batches: [] });
  api.listReferenceFolders.mockResolvedValue({ folders: [], in_docker: false });
  api.createFolder.mockResolvedValue({});
  api.patchFolder.mockResolvedValue({});
});

describe("ImportFoldersSection", () => {
  it("lists each folder with what happens to the original and its state", async () => {
    const wrapper = mountSection();
    await flushPromises();
    const row = wrapper.find(".ifs-list .ifs-row");
    expect(row.text()).toContain("ComfyUI output");
    expect(row.text()).toContain("Keep original");
    expect(row.text()).toContain("Folder not found");
    expect(row.text()).toContain("12");
  });

  it("adds a folder named after its last component when no name is given", async () => {
    const wrapper = mountSection();
    await flushPromises();
    await button(wrapper, "Add import folder…").trigger("click");
    await wrapper.find(".dialog-stub input").setValue("/home/me/Scans/");
    await wrapper
      .findAll(".dialog-stub [role='radio']")
      .find((r) => r.text() === "Delete original")
      .trigger("click");
    await button(wrapper, "Add").trigger("click");
    await flushPromises();
    expect(api.createFolder).toHaveBeenCalledWith("import", {
      folder: "/home/me/Scans/",
      label: "Scans",
      delete_after_import: true,
    });
    expect(wrapper.find(".dialog-stub").exists()).toBe(false);
  });

  it("edits the name and keeps the folder, and shows why a save failed", async () => {
    api.patchFolder.mockRejectedValueOnce({
      response: { data: { detail: "That folder overlaps a library." } },
    });
    const wrapper = mountSection();
    await flushPromises();
    await button(wrapper, "Edit…").trigger("click");
    const name = wrapper
      .findAll(".dialog-stub input")
      .find((i) => i.element.value === "ComfyUI output");
    await name.setValue("Renders");
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    expect(api.patchFolder).toHaveBeenCalledWith("import", 3, {
      label: "Renders",
      delete_after_import: false,
    });
    expect(wrapper.find(".ifs-form__error").text()).toContain("overlaps");
  });
});
