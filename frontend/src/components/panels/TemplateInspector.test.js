// The right rail on the Templates tab (#2000): what the selected template
// would download, each file on the shelf or not, and the one verb.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});
vi.mock("../../api/comfyui", () => ({
  listTemplates: vi.fn(),
  addTemplates: vi.fn(),
  templateThumbnailUrl: ({ name }) => `/thumbs/${name}`,
}));

vi.mock("../../api/modelDownloads", () => ({
  startModelDownload: vi.fn(),
  cancelModelDownload: vi.fn(),
}));

import { startModelDownload } from "../../api/modelDownloads";
import TemplateInspector from "./TemplateInspector.vue";
import { sessionContext } from "../../utils/apiClient";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";

enableAutoUnmount(afterEach);

const TEMPLATE = {
  key: "default/krea",
  module: "default",
  name: "krea",
  source: "builtin",
  title: "Flux.1 Krea Dev",
  thumbnail: true,
  size: 12 * 1024 ** 3,
  files: [
    {
      name: "flux1-krea-dev.safetensors",
      directory: "diffusion_models",
      url: "https://example.com/krea.safetensors",
      present: false,
    },
    // A link that is not a web address is never drawn as one.
    {
      name: "odd.safetensors",
      directory: "vae",
      url: "javascript:alert(1)",
      present: false,
    },
    { name: "ae.safetensors", directory: "vae", url: "https://example.com/ae", present: true },
    // One the server can fetch is downloaded here, as the run would.
    {
      name: "clip_l.safetensors",
      directory: "text_encoders",
      url: "https://example.com/clip_l",
      present: false,
      downloadable: true,
    },
  ],
};

function mounted(keys) {
  const store = useWorkflowTemplatesStore();
  store.templates = [TEMPLATE, { ...TEMPLATE, key: "default/other", name: "other" }];
  store.comfyuiUrl = "http://127.0.0.1:8188";
  store.selectRange(keys);
  const wrapper = mount(TemplateInspector, {
    global: { stubs: { Tooltip: true } },
  });
  return { wrapper, store };
}

describe("the template inspector", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("lists the files on the shelf and off it, linking only a web address", () => {
    const { wrapper } = mounted(["default/krea"]);
    expect(wrapper.find('[data-testid="tpli-title"]').text()).toBe("Flux.1 Krea Dev");
    expect(wrapper.text()).toContain("1 of 4 on your shelf");
    expect(wrapper.text()).toContain("Downloads 12.0 GB in all.");
    expect(wrapper.find('[data-testid="tpli-source"]').text()).toContain(
      "http://127.0.0.1:8188",
    );
    const rows = wrapper.findAll('[data-testid="tpli-file"]');
    expect(rows.map((row) => row.find("a").exists())).toEqual([
      true,
      false,
      false,
      false,
    ]);
    expect(
      rows.map((row) => row.find('[data-testid="model-download"]').exists()),
    ).toEqual([false, false, false, true]);
    const link = rows[0].find("a");
    expect(link.attributes("href")).toBe("https://example.com/krea.safetensors");
    expect(link.attributes("rel")).toContain("noopener");
  });

  it("starts the download in PixlStash, not in the browser", async () => {
    startModelDownload.mockResolvedValue({ id: "d1", file: "clip_l.safetensors" });
    const { wrapper } = mounted(["default/krea"]);
    await wrapper.find('[data-testid="model-download"]').trigger("click");
    expect(startModelDownload).toHaveBeenCalledWith("clip_l.safetensors");
  });

  it("keeps the link in a read-only session, which cannot download", () => {
    sessionContext.value = { scope: "READ" };
    try {
      const { wrapper } = mounted(["default/krea"]);
      const last = wrapper.findAll('[data-testid="tpli-file"]')[3];
      expect(last.find('[data-testid="model-download"]').exists()).toBe(false);
      expect(last.find("a").attributes("href")).toBe("https://example.com/clip_l");
    } finally {
      sessionContext.value = null;
    }
  });

  it("asks the view to add the selection", async () => {
    const { wrapper, store } = mounted(["default/krea", "default/other"]);
    expect(wrapper.text()).toContain("2 templates");
    const add = wrapper.find('[data-testid="tpli-add"]');
    expect(add.text()).toContain("Add 2 to my workflows");
    await add.trigger("click");
    expect(store.addRequest).toBe(1);
  });

  it("says a partner template runs with credits, and through whom", () => {
    const store = useWorkflowTemplatesStore();
    store.templates = [
      { ...TEMPLATE, source: "partner", files: [], providers: ["Example Co"] },
    ];
    store.selectRange([TEMPLATE.key]);
    const wrapper = mount(TemplateInspector, { global: { stubs: { Tooltip: true } } });
    expect(wrapper.text()).toContain(
      "Runs with credits on Comfy's partner API nodes through Example Co",
    );
  });

  // A run started on Mine is still going when the reader moves over here, and
  // this rail is the only one on the screen.
  it("offers Tasks last, with the task manager in place of the template", async () => {
    const { wrapper } = mounted(["default/krea"]);
    const band = wrapper.findAll("button.inspector-tab");
    // The stubbed icon prints its name ahead of the label.
    expect(band.map((t) => t.text().replace(/^mdi-\S+ /, ""))).toEqual([
      "Template",
      "Tasks",
    ]);

    await band[1].trigger("click");
    expect(wrapper.text()).toContain("No active tasks");
    expect(wrapper.find('[data-testid="tpli-title"]').exists()).toBe(false);
    // Add acts on the template, so it goes with it.
    expect(wrapper.find('[data-testid="tpli-add"]').exists()).toBe(false);

    await wrapper.findAll("button.inspector-tab")[0].trigger("click");
    expect(wrapper.find('[data-testid="tpli-add"]').exists()).toBe(true);
  });

  it("follows the store's deep link onto the Tasks tab", async () => {
    const { wrapper } = mounted(["default/krea"]);
    expect(wrapper.text()).not.toContain("No active tasks");
    useSidebarStore().showTasksTab();
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("No active tasks");
  });

  it("offers nothing to add with nothing selected", () => {
    const { wrapper } = mounted([]);
    expect(wrapper.find('[data-testid="tpli-add"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("Pick a template");
  });
});
