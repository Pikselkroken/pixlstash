// The Models workspace sidebar (#2168, #2203): the Model folders slot opens
// Manage model folders, the places and the Folders tree drive the shelf store,
// and the Add band asks the shelf for its dialogs.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { reactive } from "vue";

const push = vi.fn();
const route = reactive({ name: "models" });
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
import SidebarResizeHandle from "./SidebarResizeHandle.vue";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
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

function mountBar(options = {}) {
  return mount(ModelsSideBar, {
    ...options,
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
  row(wrapper, attr, value).find(".sidebar-list-count").text();

describe("ModelsSideBar", () => {
  it("can be resized, as the Media sidebar can", async () => {
    const wrapper = mountBar();
    await flushPromises();
    useUserPrefsStore().sidebarWidth = 240;
    expect(wrapper.findComponent(SidebarResizeHandle).exists()).toBe(true);
    const handle = wrapper.find(".sidebar-resize-handle");
    handle.element.dispatchEvent(
      new MouseEvent("pointerdown", { clientX: 100, bubbles: true }),
    );
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 140 }));
    await flushPromises();
    const bar = wrapper.find('[data-testid="models-sidebar"]').element;
    expect(bar.style.width).toBe("280px");
    window.dispatchEvent(new MouseEvent("pointerup", { clientX: 140 }));
    expect(useUserPrefsStore().sidebarWidth).toBe(280);
  });

  it("keeps the slot as one Model folders button that opens Manage", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const slot = wrapper.find('[data-testid="models-folder-slot"]');
    // Just the name: the tree below shows the drives and folders (#2203).
    expect(slot.text()).toBe("Model folders");
    expect(slot.attributes("aria-label")).toBe("Manage model folders");
    await slot.trigger("click");
    expect(store.shellRequest.what).toBe("manage-folders");
    expect(store.shellRequest.el).toBe(slot.element);
  });

  it("has no Limit to folder menu and no Type or Base model rows", async () => {
    const wrapper = mountBar();
    await flushPromises();
    expect(wrapper.text()).not.toContain("Limit to folder");
    // Training and Folders are the only sections under the places.
    expect(wrapper.findAll("h3").map((one) => one.text())).toEqual([
      "Training",
      "Folders",
    ]);
    expect(wrapper.find("[data-type]").exists()).toBe(false);
    expect(wrapper.find("[data-base]").exists()).toBe(false);
  });

  it("pins Training under the places and scrolls only the Folders tree", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const html = wrapper.html();
    const runs = html.indexOf('data-place="runs"');
    expect(runs).toBeGreaterThan(html.indexOf('data-place="loose"'));
    expect(html.indexOf('data-testid="model-folder-tree"')).toBeGreaterThan(
      html.indexOf('data-testid="models-sidebar-new-run"'),
    );
    // Training sits in the fixed block; the tree is alone in the scroll pane.
    const pane = wrapper.find(".msb-scroll");
    expect(pane.find('[data-testid="model-folder-tree"]').exists()).toBe(true);
    expect(pane.find('[data-place="runs"]').exists()).toBe(false);
    expect(pane.find('[data-testid="models-sidebar-new-run"]').exists()).toBe(false);
    expect(wrapper.find(".msb-fixed [data-place='runs']").exists()).toBe(true);
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

  it("gives the focus back to the Folders + when its field is cancelled", async () => {
    useLibrariesStore().canManage = true;
    const wrapper = mountBar({ attachTo: document.body });
    await flushPromises();
    await wrapper.find('[role="treeitem"][data-folder-id="2"]').trigger("click");
    const add = wrapper.find('[data-testid="models-sidebar-new-folder"]');
    await add.trigger("click");
    const field = wrapper.find('[data-testid="model-folder-name"]');
    expect(field.exists()).toBe(true);
    await field.trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(wrapper.find('[data-testid="model-folder-name"]').exists()).toBe(false);
    expect(document.activeElement).toBe(add.element);
    wrapper.unmount();
  });

  it("keeps what was typed when the Folders + is clicked again", async () => {
    useLibrariesStore().canManage = true;
    const wrapper = mountBar({ attachTo: document.body });
    await flushPromises();
    await wrapper.find('[role="treeitem"][data-folder-id="2"]').trigger("click");
    const add = wrapper.find('[data-testid="models-sidebar-new-folder"]');
    await add.trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="model-folder-name"]').setValue("flux");
    add.element.focus();
    await add.trigger("click");
    await flushPromises();
    const field = wrapper.find('[data-testid="model-folder-name"]');
    expect(field.element.value).toBe("flux");
    expect(document.activeElement).toBe(field.element);
    wrapper.unmount();
  });

  it("closes the Folders + field when Training runs hides the open folder", async () => {
    useLibrariesStore().canManage = true;
    const wrapper = mountBar();
    await flushPromises();
    await wrapper.find('[role="treeitem"][data-folder-id="2"]').trigger("click");
    await wrapper.find('[data-testid="models-sidebar-new-folder"]').trigger("click");
    expect(wrapper.find('[data-testid="model-folder-name"]').exists()).toBe(true);
    route.name = "models-runs";
    await flushPromises();
    expect(wrapper.find('[data-testid="model-folder-name"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("re-reads the open folder listings when the shelf refetches", async () => {
    const { listModelFolderDirs } = await import("../../api/modelFolders");
    mountBar();
    await flushPromises();
    listModelFolderDirs.mockClear();
    store.rows = [...ROWS];
    await flushPromises();
    expect(listModelFolderDirs.mock.calls.map((call) => call[0]).sort()).toEqual([
      1, 2,
    ]);
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

  it("leads with All models, as the other sidebars lead with their All row", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const order = wrapper
      .findAll(".msb-fixed > [data-place]")
      .map((el) => el.attributes("data-place"));
    expect(order).toEqual(["all", "favourites", "recent", "sets", "loose", "runs"]);
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

  it("opens the Train a LoRA dialog on its picker from its own row", async () => {
    const training = useTrainingStore();
    training.openToPick = vi.fn();
    const wrapper = mountBar();
    await flushPromises();
    const verb = wrapper.find("[data-testid='models-sidebar-new-run']");
    expect(verb.text()).toBe("Train a LoRA…");
    expect(verb.classes()).toContain("sidebar-list-item");
    await verb.trigger("click");
    expect(training.openToPick).toHaveBeenCalled();
  });

  it("names the live run and its progress on the runs row", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const status = () =>
      wrapper.find("[data-testid='models-sidebar-runs-status']");
    useTasksStore().resources = {
      leases: [
        {
          tenant: "aitoolkit",
          state: "granted",
          job_id: "u1",
          label: "LoRA training 'aria_v4'",
          progress: { step: 41, total: 100 },
        },
      ],
    };
    await flushPromises();
    // The job's name, not the lease's Tasks-tab label.
    expect(status().text()).toBe("aria_v4 · 41%");
    expect(row(wrapper, "place", "runs").classes()).toContain("msb-row--two");
  });

  it("names a probe or preview pass rather than its steps as a percentage", async () => {
    const wrapper = mountBar();
    await flushPromises();
    const lease = (progress) => ({
      leases: [{ tenant: "aitoolkit", state: "granted", job_id: "u1", progress }],
    });
    const status = () =>
      wrapper.find("[data-testid='models-sidebar-runs-status']").text();
    // The memory probe before training finishes its own 6 of 6 steps.
    useTasksStore().resources = lease({ step: 6, total: 6, shown_as: "checking" });
    await flushPromises();
    expect(status()).toBe("aria_v4 · checking");
    useTasksStore().resources = lease({ step: 40, total: 40, shown_as: "previews" });
    await flushPromises();
    expect(status()).toBe("aria_v4 · previews");
  });

  it("reads the jobs again for a live run it has not seen", async () => {
    const { listTrainingJobs } = await import("../../api/training");
    const wrapper = mountBar();
    await flushPromises();
    listTrainingJobs.mockClear();
    // The tasks poll replaces the lease object on every tick.
    for (let tick = 0; tick < 3; tick += 1) {
      useTasksStore().resources = {
        leases: [{ tenant: "aitoolkit", state: "granted", job_id: "u9" }],
      };
      await flushPromises();
    }
    expect(listTrainingJobs).toHaveBeenCalledTimes(1);
    // Still unknown after the read: no loop, and a plain line meanwhile.
    expect(
      wrapper.find("[data-testid='models-sidebar-runs-status']").text(),
    ).toBe("A run");
  });

  it("says a run queued for the card is waiting, not training", async () => {
    const wrapper = mountBar();
    await flushPromises();
    useTasksStore().resources = {
      leases: [
        { tenant: "aitoolkit", state: "waiting", job_id: "u1" },
        { tenant: "comfyui", state: "granted" },
      ],
    };
    await flushPromises();
    expect(
      wrapper.find("[data-testid='models-sidebar-runs-status']").text(),
    ).toBe("aria_v4 · waiting");
  });

  it("reads a job's naive time as UTC for the last run", async () => {
    const { useTrainingRunsStore } = await import(
      "../../stores/useTrainingRunsStore"
    );
    const wrapper = mountBar();
    await flushPromises();
    const runs = useTrainingRunsStore();
    runs.runs = [];
    // A failed job that never got a folder: its card is dated by created_at,
    // which the server writes without a zone.
    const at = new Date(Date.now() - 3 * 3600 * 1000 - 60_000)
      .toISOString()
      .replace("Z", "");
    runs.jobs = [
      { id: 2, uuid: "u2", name: "x", status: "error", created_at: at },
    ];
    await flushPromises();
    expect(
      wrapper.find("[data-testid='models-sidebar-runs-status']").text(),
    ).toBe("Last run 3h ago");
  });

  it("says when the last run was while nothing trains", async () => {
    const { useTrainingRunsStore } = await import(
      "../../stores/useTrainingRunsStore"
    );
    const wrapper = mountBar();
    await flushPromises();
    const runs = useTrainingRunsStore();
    // Nothing dated: no second line, and the row keeps one line's height.
    expect(
      wrapper.find("[data-testid='models-sidebar-runs-status']").exists(),
    ).toBe(false);
    expect(row(wrapper, "place", "runs").classes()).not.toContain(
      "msb-row--two",
    );
    const twoHoursAgo = Date.now() / 1000 - 2 * 3600 - 60;
    runs.runs = [
      { name: "aria_v3", modified_at: twoHoursAgo - 86400, checkpoints: [] },
      { name: "walter_v2", modified_at: twoHoursAgo, checkpoints: [] },
    ];
    await flushPromises();
    expect(
      wrapper.find("[data-testid='models-sidebar-runs-status']").text(),
    ).toBe("Last run 2h ago");
  });
});
