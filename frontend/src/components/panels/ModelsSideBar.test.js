// The Models workspace sidebar (#2168): the folder slot narrows the list and
// the counts, the places and the Type / Base rows drive the shelf store, and
// the Add band asks the shelf for its dialogs.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const push = vi.fn();
const route = { name: "models" };
vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ push }),
}));

vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolders: vi.fn(async () => FOLDERS),
  listModelFolderDevices: vi.fn(async () => DEVICES),
}));

vi.mock("../../api/modelShelf", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchWorkflowSets: vi.fn(async () => ({
    combinations: [],
    no_set: [],
    hand_made: [
      {
        id: 7,
        name: "Flux kit",
        members: [{ id: 1, on_shelf: true, sha256: "a" }],
      },
    ],
    set_checks: [],
    hidden_sets: [],
    hidden_models: [],
  })),
}));

import ModelsSideBar from "./ModelsSideBar.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const FOLDERS = [
  { id: 1, path: "/home/me/ComfyUI/models", kind: "user", present_bytes: 9e9 },
  { id: 2, path: "/home/me/archive/loras", kind: "user", present_bytes: 3e8 },
  { id: 3, path: "/home/me/ai-toolkit/output", kind: "source", present_bytes: 0 },
];
const DEVICES = [
  {
    device_id: 11,
    mount_point: "/",
    total_bytes: 1e12,
    free_bytes: 50 * 1024 ** 3,
    folder_ids: [1, 2],
  },
];

const at = (folderId) => [{ folder_id: folderId, state: "present" }];
const ROWS = [
  { id: 1, file_kind: "checkpoint", base_model: "Flux.1 dev", locations: at(1), sha256: "a" },
  { id: 2, file_kind: "adapter", kind: "lora", base_model: "Flux.1 dev", locations: at(2), sha256: "b" },
  { id: 3, file_kind: "vae", base_model: "SDXL", locations: at(1), sha256: "c" },
];

function mountBar() {
  return mount(ModelsSideBar, {
    global: {
      stubs: {
        "v-icon": true,
        Tooltip: true,
        AiToolkitIcon: true,
        "v-menu": {
          template:
            "<div><slot name='activator' :props='{}' /><slot /></div>",
        },
      },
    },
  });
}

let store;

beforeEach(async () => {
  setActivePinia(createPinia());
  push.mockClear();
  route.name = "models";
  store = useModelShelfStore();
  store.rows = ROWS;
  store.setPlace("sets");
});

const row = (wrapper, attr, value) =>
  wrapper.find(`[data-${attr}="${value}"]`);
const countOf = (wrapper, attr, value) =>
  row(wrapper, attr, value).find(".msb-count").text();

describe("ModelsSideBar", () => {
  it("lists the shelf's folders with their size and drive, not ai-toolkit's", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const ids = wrapper
      .findAll(".msb-folder")
      .map((one) => one.attributes("data-folder-id"));
    expect(ids).toEqual(["all", "1", "2"]);
    const archive = wrapper.find('[data-folder-id="2"]');
    expect(archive.text()).toContain("/home/me/archive/loras");
    expect(archive.text()).toContain("50.0 GB free");
    expect(wrapper.find(".msb-slot-name").text()).toBe("All folders");
  });

  it("narrows the counts to the chosen folder and leaves the set grid", async () => {
    const wrapper = mountBar();
    await flushPromises();
    expect(countOf(wrapper, "place", "all")).toBe("3");
    expect(countOf(wrapper, "type", "lora")).toBe("1");

    await wrapper.find('[data-folder-id="1"]').trigger("click");

    expect(store.folderScope).toBe(1);
    expect(store.place).toBe("all");
    expect(countOf(wrapper, "place", "all")).toBe("2");
    expect(countOf(wrapper, "type", "lora")).toBe("0");
    expect(store.visibleRows.map((one) => one.id).sort()).toEqual([1, 3]);
    expect(wrapper.find(".msb-slot-name").text()).toBe("models");
  });

  it("counts In no set yet once the sets are read", async () => {
    const wrapper = mountBar();
    await flushPromises();
    expect(countOf(wrapper, "place", "loose")).toBe("2");
    expect(countOf(wrapper, "place", "sets")).toBe("1");
  });

  it("opens a place and lights only its row", async () => {
    const wrapper = mountBar();
    await flushPromises();
    await row(wrapper, "place", "favourites").trigger("click");
    expect(store.place).toBe("favourites");
    expect(store.view.groupBy).toBe(store.view.listGroupBy);
    expect(row(wrapper, "place", "favourites").attributes("aria-current")).toBe(
      "page",
    );
    expect(row(wrapper, "place", "sets").attributes("aria-current")).toBe(
      undefined,
    );
  });

  it("toggles a Type row as the one sidebar filter", async () => {
    const wrapper = mountBar();
    await flushPromises();
    await row(wrapper, "type", "vae").trigger("click");
    expect(store.sidebarFilter).toEqual({ axis: "type", key: "vae" });
    expect(store.place).toBe("all");
    expect(store.visibleRows.map((one) => one.id)).toEqual([3]);
    expect(row(wrapper, "type", "vae").attributes("aria-current")).toBe("page");
    // No place lights while a Type row does.
    expect(row(wrapper, "place", "all").attributes("aria-current")).toBe(
      undefined,
    );
    await row(wrapper, "type", "vae").trigger("click");
    expect(store.sidebarFilter).toBe(null);
  });

  it("filters by a base model row", async () => {
    const wrapper = mountBar();
    await flushPromises();
    await row(wrapper, "base", "SDXL").trigger("click");
    expect(store.visibleRows.map((one) => one.id)).toEqual([3]);
  });

  it("asks the shelf for its dialogs", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const items = wrapper.findAll(".ctx-item");
    const byText = (text) => items.find((one) => one.text().includes(text));
    await byText("Add file…").trigger("click");
    expect(store.shellRequest.what).toBe("add-file");
    await byText("Manage model folders…").trigger("click");
    expect(store.shellRequest.what).toBe("manage-folders");
    // ai-toolkit's root is registered, so the band does not offer to set it.
    expect(byText("Set ai-toolkit output folder…")).toBe(undefined);
  });

  it("opens Training runs as a route and lights it there", async () => {
    const wrapper = mountBar();
    await flushPromises();
    await row(wrapper, "place", "runs").trigger("click");
    expect(push).toHaveBeenCalledWith({ name: "models-runs" });
  });
});
