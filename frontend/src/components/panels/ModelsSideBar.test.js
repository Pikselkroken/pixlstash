// The Models workspace sidebar (#2168, #2203): the Model folders slot opens
// Manage model folders, the places and the Folders tree drive the shelf store,
// and the Add band asks the shelf for its dialogs.

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
  listModelFolderDirs: vi.fn(async (id, path) => ({
    folder_id: id,
    path,
    locked: false,
    model_count: 2,
    bytes: 0,
    dirs: [],
  })),
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

vi.mock("../../api/modelImports", async (importOriginal) => ({
  ...(await importOriginal()),
  listRuns: vi.fn(async () => [
    { name: "aria_v3", checkpoints: [{ filename: "a.safetensors", step: null }] },
    { name: "walter_v2", checkpoints: [{ filename: "w.safetensors", step: null }] },
  ]),
}));

vi.mock("../../api/training", async (importOriginal) => ({
  ...(await importOriginal()),
  listTrainingJobs: vi.fn(async () => [
    { id: 1, uuid: "u1", name: "aria_v4", status: "running", step: 5, run_name: null },
  ]),
}));

import ModelsSideBar from "./ModelsSideBar.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useTrainingStore } from "../../stores/useTrainingStore";

const FOLDERS = [
  { id: 1, path: "/home/me/ComfyUI/models", kind: "user", movable: "per_item", present_bytes: 9e9 },
  { id: 2, path: "/home/me/archive/loras", kind: "user", movable: "per_item", present_bytes: 3e8 },
  { id: 3, path: "/home/me/ai-toolkit/output", kind: "source", movable: "external", present_bytes: 0 },
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

const at = (folderId) => [
  { folder_id: folderId, relpath: "m.safetensors", state: "present" },
];
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
  it("keeps the slot as one Model folders button that opens Manage", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const slot = wrapper.find('[data-testid="models-folder-slot"]');
    // ai-toolkit's root is taken from, not catalogued: two folders, one drive.
    expect(slot.text()).toContain("Model folders");
    expect(slot.text()).toContain("1 drive · 2 folders");
    await slot.trigger("click");
    expect(store.shellRequest.what).toBe("manage-folders");
    expect(store.shellRequest.el).toBe(slot.element);
  });

  it("has no Limit to folder menu and no Type or Base model rows", async () => {
    const wrapper = mountBar();
    await flushPromises();
    expect(wrapper.text()).not.toContain("Limit to folder");
    expect(wrapper.find("[data-type]").exists()).toBe(false);
    expect(wrapper.find("[data-base]").exists()).toBe(false);
  });

  it("draws the Folders tree between the places and Training", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const html = wrapper.html();
    const tree = html.indexOf('data-testid="model-folder-tree"');
    expect(tree).toBeGreaterThan(html.indexOf('data-place="loose"'));
    expect(tree).toBeLessThan(html.indexOf('data-place="runs"'));
    const folders = wrapper
      .findAll('[role="treeitem"][data-folder-id]')
      .map((one) => one.attributes("data-folder-id"));
    expect(folders).toEqual(["1", "2"]);
  });

  it("opens a folder from the tree and lights it, not a place", async () => {
    const wrapper = mountBar();
    await flushPromises();
    await wrapper.find('[role="treeitem"][data-folder-id="2"]').trigger("click");
    expect(store.place).toBe("folder");
    expect(store.folderScope).toEqual({ folderId: 2, path: "", locked: false });
    expect(store.visibleRows.map((one) => one.id)).toEqual([2]);
    const lit = wrapper.find('[role="treeitem"][data-folder-id="2"]');
    expect(lit.attributes("aria-selected")).toBe("true");
    expect(row(wrapper, "place", "all").attributes("aria-current")).toBe(
      undefined,
    );
    // All models leaves the folder.
    await row(wrapper, "place", "all").trigger("click");
    expect(store.folderScope).toBe(null);
    expect(
      wrapper
        .find('[role="treeitem"][data-folder-id="2"]')
        .attributes("aria-selected"),
    ).toBe("false");
  });

  it("leaves Training runs for the list when a folder is opened", async () => {
    route.name = "models-runs";
    const wrapper = mountBar();
    await flushPromises();
    await wrapper.find('[role="treeitem"][data-folder-id="1"]').trigger("click");
    expect(push).toHaveBeenCalledWith({ name: "models" });
  });

  it("closes a folder that is no longer registered", async () => {
    store.setFolderScope({ folderId: 99, path: "" });
    expect(store.place).toBe("folder");
    mountBar();
    await flushPromises();
    expect(store.folderScope).toBe(null);
    expect(store.place).toBe("all");
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

  it("asks the shelf for its dialogs", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const items = wrapper.findAll(".ctx-item");
    const byText = (text) => items.find((one) => one.text().includes(text));
    await byText("Add file…").trigger("click");
    expect(store.shellRequest.what).toBe("add-file");
    await byText("Add folder…").trigger("click");
    expect(store.shellRequest.what).toBe("add-folder");
    // ai-toolkit's root is registered, so the band does not offer to set it.
    expect(byText("Set ai-toolkit output folder…")).toBe(undefined);
  });

  it("opens Training runs as a route and lights it there", async () => {
    const wrapper = mountBar();
    await flushPromises();
    await row(wrapper, "place", "runs").trigger("click");
    expect(push).toHaveBeenCalledWith({ name: "models-runs" });
  });

  it("counts the training runs, and lights the dot from the polled leases", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const runs = wrapper.find('[data-place="runs"]');
    // Two in the folder, one queued run that has no folder yet.
    expect(runs.find("[data-testid='models-sidebar-runs-count']").text()).toBe("3");
    // The runs store's job list is not re-read off the runs view, so it does
    // not light the dot: only an AI Toolkit lease does.
    expect(runs.find("[data-testid='models-sidebar-live-dot']").exists()).toBe(false);
    useTasksStore().resources = { leases: [{ tenant: "aitoolkit" }] };
    await flushPromises();
    expect(runs.find("[data-testid='models-sidebar-live-dot']").exists()).toBe(true);
  });

  it("opens the Train a LoRA dialog on its picker from the Training +", async () => {
    const training = useTrainingStore();
    training.openToPick = vi.fn();
    const wrapper = mountBar();
    await flushPromises();
    await wrapper.find("[data-testid='models-sidebar-new-run']").trigger("click");
    expect(training.openToPick).toHaveBeenCalled();
  });
});
