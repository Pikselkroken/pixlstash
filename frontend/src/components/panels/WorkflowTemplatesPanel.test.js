// The Templates tab notices a ComfyUI that started after PixlStash did, and
// its card menu does what the pill does.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, shallowMount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import WorkflowTemplatesPanel from "./WorkflowTemplatesPanel.vue";
import { startModelDownload } from "../../api/modelDownloads";
import { useTasksStore } from "../../stores/useTasksStore";
import { sessionContext } from "../../utils/apiClient";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";

const listTemplates = vi.fn();
vi.mock("../../api/comfyui", () => ({
  listTemplates: (...args) => listTemplates(...args),
  templateThumbnailUrl: () => "",
}));
vi.mock("../../api/modelDownloads", () => ({
  startModelDownload: vi.fn(),
  cancelModelDownload: vi.fn(),
}));
vi.mock("../../api/workers", () => ({ getWorkerProgress: vi.fn() }));

describe("WorkflowTemplatesPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    listTemplates.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("asks ComfyUI again every 10 s while it has not answered, and stops when closed", async () => {
    listTemplates.mockResolvedValue({ reachable: false, detail: "refused" });
    const store = useWorkflowTemplatesStore();
    await store.fetchTemplates();
    const wrapper = shallowMount(WorkflowTemplatesPanel, {
      props: { columnMin: 240, columnGap: 16 },
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listTemplates).toHaveBeenCalledTimes(2);

    listTemplates.mockResolvedValue({ reachable: true, templates: [] });
    await vi.advanceTimersByTimeAsync(10_000);
    await flushPromises();
    expect(store.reachable).toBe(true);
    // Answered: nothing more to ask.
    await vi.advanceTimersByTimeAsync(30_000);
    expect(listTemplates).toHaveBeenCalledTimes(3);

    store.reachable = false;
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(listTemplates).toHaveBeenCalledTimes(3);
  });
});

// The card menu: everything the pill does, and the download of what the
// selection lacks.
describe("WorkflowTemplatesPanel card menu", () => {
  const MenuStub = {
    name: "VMenu",
    props: ["modelValue"],
    template:
      '<div v-if="modelValue" class="menu-stub"><slot /></div>',
  };
  const TooltipStub = {
    name: "Tooltip",
    props: ["text"],
    template: '<span class="tip" :data-text="text" />',
  };
  const file = (name, extra = {}) => ({
    name,
    directory: "vae",
    present: false,
    downloadable: false,
    ...extra,
  });
  // `downloadable` is the server's lookup by name, present or not.
  const TEMPLATES = [
    {
      key: "default/a",
      title: "A",
      files: [
        file("shared.safetensors", { downloadable: true }),
        file("only-a.safetensors", { downloadable: true }),
        file("on-shelf.safetensors", { present: true, downloadable: true }),
      ],
    },
    {
      key: "default/b",
      title: "B",
      files: [
        file("shared.safetensors", { downloadable: true }),
        file("unknown.safetensors"),
      ],
    },
    { key: "default/c", title: "C", files: [file("unknown.safetensors")] },
    {
      key: "default/d",
      title: "D",
      files: [file("on-shelf.safetensors", { present: true, downloadable: true })],
    },
  ];

  let store;
  async function mounted(selection = []) {
    listTemplates.mockResolvedValue({ reachable: true, templates: TEMPLATES });
    store = useWorkflowTemplatesStore();
    await store.fetchTemplates();
    store.selectRange(selection);
    return mount(WorkflowTemplatesPanel, {
      props: { columnMin: 240, columnGap: 16 },
      global: {
        stubs: {
          "v-menu": MenuStub,
          "v-icon": true,
          Tooltip: TooltipStub,
          WorkflowTemplateCard: true,
          FilterStrip: true,
          ComfyuiIcon: true,
          AppButton: true,
          AppBarButton: true,
        },
      },
    });
  }
  const row = (wrapper, key) => wrapper.find(`.wtp-row[data-key="${key}"]`);
  const menuRow = (wrapper, verb) =>
    wrapper.find(`[data-testid="wtp-menu"] [data-verb="${verb}"]`);

  beforeEach(() => {
    setActivePinia(createPinia());
    listTemplates.mockReset();
    startModelDownload.mockReset();
  });

  it("right-click on an unselected card selects it alone and offers Add", async () => {
    const wrapper = await mounted(["default/b"]);
    await row(wrapper, "default/a").trigger("contextmenu");
    expect(store.selectedKeys).toEqual(["default/a"]);
    expect(menuRow(wrapper, "add").text()).toBe("Add to my workflows");
    await menuRow(wrapper, "add").trigger("click");
    expect(wrapper.emitted("add")).toHaveLength(1);
    expect(wrapper.find('[data-testid="wtp-menu"]').exists()).toBe(false);
  });

  it("right-click inside the selection keeps all of it", async () => {
    const wrapper = await mounted(["default/a", "default/b"]);
    await row(wrapper, "default/b").trigger("contextmenu");
    expect(store.selectedKeys).toEqual(["default/a", "default/b"]);
    expect(menuRow(wrapper, "add").text()).toBe("Add 2 to my workflows");
  });

  it("downloads each linked missing file once, and nothing else", async () => {
    startModelDownload.mockImplementation(async (name) => ({
      id: name,
      file: name,
      status: "running",
    }));
    const wrapper = await mounted(["default/a", "default/b"]);
    await row(wrapper, "default/a").trigger("contextmenu");
    const download = menuRow(wrapper, "download-missing");
    expect(download.text()).toBe("Download 2 missing models");
    expect(download.attributes("aria-disabled")).toBeUndefined();
    await download.trigger("click");
    await flushPromises();
    expect(startModelDownload.mock.calls.map(([name]) => name)).toEqual([
      "shared.safetensors",
      "only-a.safetensors",
    ]);
  });

  it.each([
    [
      "default/c",
      "PixlStash does not know where to get the files missing from your shelf",
    ],
    ["default/d", "Every file they load is on your shelf"],
  ])("refuses the download for %s, saying why", async (key, why) => {
    const wrapper = await mounted([key]);
    await row(wrapper, key).trigger("contextmenu");
    const download = menuRow(wrapper, "download-missing");
    expect(download.attributes("aria-disabled")).toBe("true");
    expect(download.find(".tip").attributes("data-text")).toBe(why);
    await download.trigger("click");
    await flushPromises();
    expect(startModelDownload).not.toHaveBeenCalled();
  });

  it("leaves out a file already downloading, and says so when that is all", async () => {
    const wrapper = await mounted(["default/b"]);
    useTasksStore().downloadStarted({
      id: "s",
      file: "shared.safetensors",
      status: "running",
    });
    await row(wrapper, "default/b").trigger("contextmenu");
    const download = menuRow(wrapper, "download-missing");
    expect(download.attributes("aria-disabled")).toBe("true");
    expect(download.find(".tip").attributes("data-text")).toBe(
      "Already downloading: the Tasks tab shows how far",
    );
  });

  it("offers no download in a read-only session, saying why", async () => {
    sessionContext.value = { scope: "READ" };
    try {
      const wrapper = await mounted(["default/a"]);
      await row(wrapper, "default/a").trigger("contextmenu");
      const download = menuRow(wrapper, "download-missing");
      expect(download.attributes("aria-disabled")).toBe("true");
      expect(download.find(".tip").attributes("data-text")).toBe(
        "This session cannot download models",
      );
    } finally {
      sessionContext.value = null;
    }
  });

  it("opens from the Menu key and Shift+F10 on the cursor card", async () => {
    const wrapper = await mounted();
    // The cursor starts on the first card in the sorted order.
    const first = store.sortedTemplates[0].key;
    await row(wrapper, first).trigger("keydown", { key: "ContextMenu" });
    expect(store.selectedKeys).toEqual([first]);
    expect(wrapper.find('[data-testid="wtp-menu"]').exists()).toBe(true);
    await menuRow(wrapper, "clear").trigger("click");
    expect(store.selectedKeys).toEqual([]);
    await row(wrapper, first).trigger("keydown", {
      key: "F10",
      shiftKey: true,
    });
    expect(wrapper.find('[data-testid="wtp-menu"]').exists()).toBe(true);
  });
});
