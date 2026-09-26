// The lightbox Edit tab's body (#1381): which workflows it lists, what Run
// sends, and that the lightbox stays on the original afterwards.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const listWorkflowCards = vi.fn();
const runWorkflowCard = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...a) => listWorkflowCards(...a),
  runWorkflowCard: (...a) => runWorkflowCard(...a),
}));
vi.mock("../../api/pictures", () => ({
  getPictureMetadata: vi.fn(async () => ({ stack_id: null })),
  pictureThumbnailUrl: (id) => `/thumb/${id}`,
}));
vi.mock("../../api/stacks", () => ({ listStackPictures: vi.fn(async () => []) }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../../utils/apiClient", () => ({
  onSessionReset: () => () => {},
}));

import OverlayEditPanel from "./OverlayEditPanel.vue";
import { useRunDialogStore } from "../../stores/useRunDialogStore";

enableAutoUnmount(afterEach);

const CARDS = [
  { key: "t2i", name: "Portrait", type: "txt2img", type_label: "Text to Image" },
  { key: "up", name: "4x", type: "upscale", type_label: "Upscale" },
  { key: "out", name: "Widen", type: "outpaint", type_label: "Outpaint" },
  { key: "i2i", name: "Relight", type: "img2img", type_label: "Image to Image" },
];

const flush = () => new Promise((r) => setTimeout(r, 0));

async function mountPanel() {
  const wrapper = mount(OverlayEditPanel, {
    props: { pictureId: 7, active: true },
    global: {
      stubs: {
        AppButton: { template: "<button class='run' @click=\"$emit('click')\"><slot /></button>" },
        "v-icon": true,
      },
    },
  });
  await flush();
  await flush();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listWorkflowCards.mockReset().mockResolvedValue({ cards: CARDS });
  runWorkflowCard
    .mockReset()
    .mockResolvedValue({ prompts: [{ prompt_id: "p1" }], groups: [] });
});

describe("the Edit tab", () => {
  it("lists only the edit card types, an Image to Image card first", async () => {
    const wrapper = await mountPanel();
    const options = wrapper.findAll("option").map((o) => o.element.value);
    expect(options).toEqual(["out", "i2i"]);
    expect(listWorkflowCards).toHaveBeenCalledWith({ includeOneOffs: true });
    expect(wrapper.find("select").element.value).toBe("i2i");
  });

  it("says why when there is nothing to run the picture through", async () => {
    listWorkflowCards.mockResolvedValue({ cards: CARDS.slice(0, 2) });
    const wrapper = await mountPanel();
    expect(wrapper.find("select").exists()).toBe(false);
    expect(wrapper.text()).toContain("Open Workflows");
  });

  it("runs the picture through the chosen card with the instruction", async () => {
    const wrapper = await mountPanel();
    const started = vi.spyOn(useRunDialogStore(), "started");
    await wrapper.find("textarea").setValue("  warmer light ");
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({
        picture_ids: [7],
        target: "i2i",
        prompt: "warmer light",
        stack: true,
      }),
    );
    // No source picture: that is what would make the runner step the
    // lightbox to the output, and this design stays on the original.
    expect(started).toHaveBeenCalledWith([{ prompt_id: "p1" }], []);
    expect(wrapper.text()).toContain("Running");
  });

  it("files the output into the set in view, as the Run popup does", async () => {
    const wrapper = await mountPanel();
    useRunDialogStore().context = { set_id: 4 };
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard.mock.calls[0][0].destination).toEqual({
      set_id: 4,
      project_id: null,
      character_id: null,
    });
  });

  it("leaves the workflow's own prompt alone when the box is empty", async () => {
    const wrapper = await mountPanel();
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard.mock.calls[0][0].prompt).toBeNull();
  });

  it("remembers the card it ran, and defaults to it next time", async () => {
    const first = await mountPanel();
    await first.find("select").setValue("out");
    await first.find("button.run").trigger("click");
    await flush();
    first.unmount();
    const second = await mountPanel();
    expect(second.find("select").element.value).toBe("out");
  });

  it("says why nothing was queued", async () => {
    runWorkflowCard.mockResolvedValue({
      prompts: [],
      groups: [{ reasons: [{ code: "picture_input_unfilled", inputs: [] }] }],
    });
    const wrapper = await mountPanel();
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(wrapper.text()).toContain("Nothing was queued");
    expect(wrapper.text()).not.toContain("Running");
  });

  it("marks the run finished when ComfyUI completes", async () => {
    const wrapper = await mountPanel();
    await wrapper.find("textarea").setValue("golden hour");
    await wrapper.find("button.run").trigger("click");
    await flush();
    await wrapper.setProps({ comfyuiProgress: { status: "completed" } });
    expect(wrapper.text()).toContain("Last edit");
    expect(wrapper.text()).toContain("“golden hour”");
  });
});
