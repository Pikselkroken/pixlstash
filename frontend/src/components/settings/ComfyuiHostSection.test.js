// Settings › ComfyUI Host: the ComfyUI-PixlStash node pack status line.
//
// Only a definite answer is shown: installed, or not found with how to get it.
// No ComfyUI configured, or one that cannot be asked, shows nothing.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VCard: { template: "<div><slot /></div>" },
  VDialog: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  VIcon: { template: "<i />" },
  VProgressCircular: { template: "<i />" },
}));

const config = vi.hoisted(() => ({
  getUserConfig: vi.fn(),
  patchUserConfig: vi.fn(),
}));
vi.mock("../../api/config", () => config);
const comfyui = vi.hoisted(() => ({ getPixlstashNode: vi.fn() }));
vi.mock("../../api/comfyui", () => comfyui);
vi.mock("../../stores/useFilterStore", () => ({
  useFilterStore: () => ({ comfyuiUrl: "" }),
}));

import ComfyuiHostSection from "./ComfyuiHostSection.vue";

async function mountPane() {
  const wrapper = mount(ComfyuiHostSection, { props: { open: true } });
  await flushPromises();
  return wrapper;
}

const status = (wrapper) => wrapper.find('[data-testid="comfyui-pack-status"]');

beforeEach(() => {
  config.getUserConfig.mockReset();
  comfyui.getPixlstashNode.mockReset();
  config.getUserConfig.mockResolvedValue({
    comfyui_url: "http://127.0.0.1:8188/",
  });
});

describe("ComfyUI-PixlStash status line", () => {
  it("says installed when ComfyUI has the pack", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
    const line = status(await mountPane());
    expect(line.text()).toBe("ComfyUI-PixlStash node pack: installed.");
  });

  it("says not found, with the install link, when ComfyUI lacks it", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    const line = status(await mountPane());
    expect(line.text()).toContain("not found, or too old");
    expect(line.text()).toContain("then restart ComfyUI");
    const link = line.find("a");
    expect(link.attributes("href")).toBe(
      "https://github.com/Pikselkroken/ComfyUI-PixlStash",
    );
    expect(link.attributes("target")).toBe("_blank");
  });

  it("says nothing when ComfyUI cannot be asked", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: null });
    expect(status(await mountPane()).exists()).toBe(false);
  });

  it("says nothing when asking fails", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    comfyui.getPixlstashNode.mockRejectedValue(new Error("offline"));
    expect(status(await mountPane()).exists()).toBe(false);
  });

  it("keeps the newest answer when an older one lands late", async () => {
    let answerFirst;
    comfyui.getPixlstashNode
      .mockReturnValueOnce(new Promise((r) => (answerFirst = r)))
      .mockResolvedValueOnce({ can_open_workflows: true });
    const wrapper = await mountPane();
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await flushPromises();
    answerFirst({ can_open_workflows: false });
    await flushPromises();
    expect(status(wrapper).text()).toBe(
      "ComfyUI-PixlStash node pack: installed.",
    );
  });

  it("asks nothing while no ComfyUI is configured", async () => {
    config.getUserConfig.mockResolvedValue({ comfyui_url: null });
    const wrapper = await mountPane();
    expect(comfyui.getPixlstashNode).not.toHaveBeenCalled();
    expect(status(wrapper).exists()).toBe(false);
  });
});
