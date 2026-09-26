// The lightbox Edit tab's body (#1381): which workflows it lists, what Run
// sends, and that the lightbox stays on the original afterwards.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const listWorkflowCards = vi.fn();
const runWorkflowCard = vi.fn();
const preflightWorkflowRun = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...a) => listWorkflowCards(...a),
  preflightWorkflowRun: (...a) => preflightWorkflowRun(...a),
  runWorkflowCard: (...a) => runWorkflowCard(...a),
}));
vi.mock("../../api/pictures", () => ({
  getPictureMetadata: vi.fn(async () => ({ stack_id: null })),
  pictureThumbnailUrl: (id) => `/thumb/${id}`,
}));
vi.mock("../../api/stacks", () => ({ listStackPictures: vi.fn(async () => []) }));
const push = vi.fn();
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
vi.mock("../../utils/apiClient", () => ({
  onSessionReset: () => () => {},
}));

import OverlayEditPanel from "./OverlayEditPanel.vue";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useLibrariesStore } from "../../stores/useLibrariesStore";

enableAutoUnmount(afterEach);

const CARDS = [
  { key: "t2i", name: "Portrait", type: "txt2img", type_label: "Text to Image" },
  { key: "up", name: "4x", type: "upscale", type_label: "Upscale" },
  { key: "out", name: "Widen", type: "outpaint", type_label: "Outpaint" },
  { key: "i2i", name: "Relight", type: "img2img", type_label: "Image to Image" },
];

const checkedRow = (wrapper) =>
  wrapper.find("[role=menuitemradio][aria-checked=true]").text();

const flush = () => new Promise((r) => setTimeout(r, 0));

async function mountPanel() {
  const wrapper = mount(OverlayEditPanel, {
    props: { pictureId: 7, active: true },
    global: {
      stubs: {
        AppButton: { template: "<button class='run' @click=\"$emit('click')\"><slot /></button>" },
        "v-icon": true,
        Tooltip: true,
        // Renders the field and the menu side by side, so the rows can be
        // read and clicked without Vuetify's overlay.
        "v-menu": {
          template:
            "<div><slot name='activator' :props='{}' /><slot /></div>",
        },
      },
    },
  });
  await flush();
  await flush();
  return wrapper;
}

beforeEach(async () => {
  setActivePinia(createPinia());
  // The Show it tests reprogram these; every test starts from "no stack".
  const pictures = await import("../../api/pictures");
  const stacks = await import("../../api/stacks");
  pictures.getPictureMetadata.mockReset().mockResolvedValue({ stack_id: null });
  stacks.listStackPictures.mockReset().mockResolvedValue([]);
  window.localStorage.clear();
  push.mockReset();
  listWorkflowCards.mockReset().mockResolvedValue({ cards: CARDS });
  preflightWorkflowRun.mockReset().mockResolvedValue({ ok: true, groups: [] });
  runWorkflowCard
    .mockReset()
    .mockResolvedValue({ prompts: [{ prompt_id: "p1" }], groups: [] });
});

describe("the Edit tab", () => {
  it("opens the chosen workflow in the Workflows view", async () => {
    const wrapper = await mountPanel();
    const out = wrapper
      .findAll("[role=menuitemradio]")
      .find((row) => row.text().includes("Widen"));
    await out.trigger("click");
    await wrapper.find(".edit-sec-act").trigger("click");
    expect(push).toHaveBeenCalledWith({
      name: "workflows",
      query: { card: "out" },
    });
  });

  it("lists only the edit card types, an Image to Image card first", async () => {
    const wrapper = await mountPanel();
    const rows = wrapper.findAll("[role=menuitemradio]").map((row) => row.text());
    expect(rows).toEqual(["Widen, Outpaint", "Relight, Image to Image"]);
    // The grid's default list: stack covers, one-offs left out.
    expect(listWorkflowCards).toHaveBeenCalledWith();
    expect(checkedRow(wrapper)).toBe("Relight, Image to Image");
  });

  it("says why when there is nothing to run the picture through", async () => {
    listWorkflowCards.mockResolvedValue({ cards: CARDS.slice(0, 2) });
    const wrapper = await mountPanel();
    expect(wrapper.find("[role=menu]").exists()).toBe(false);
    expect(wrapper.text()).toContain("Open Workflows");
  });

  it("leaves out an edit card the pre-flight says cannot run", async () => {
    preflightWorkflowRun.mockImplementation(async ({ target }) => ({
      groups: [
        {
          reasons:
            target === "i2i"
              ? [{ code: "missing_models", models: [] }]
              : [],
        },
      ],
    }));
    const wrapper = await mountPanel();
    const rows = wrapper.findAll("[role=menuitemradio]").map((row) => row.text());
    expect(rows).toEqual(["Widen, Outpaint"]);
    // Asked the way the run asks: this picture, that card.
    expect(preflightWorkflowRun).toHaveBeenCalledWith({
      picture_ids: [7],
      target: "i2i",
    });
  });

  it("keeps every card when ComfyUI cannot be asked, or the check fails", async () => {
    preflightWorkflowRun.mockImplementation(async ({ target }) => {
      if (target === "out") throw new Error("offline");
      return { groups: [{ reasons: [{ code: "comfyui_unreachable" }] }] };
    });
    const wrapper = await mountPanel();
    expect(wrapper.findAll("[role=menuitemradio]")).toHaveLength(2);
  });

  it("says the edit cards cannot run when every one was left out", async () => {
    preflightWorkflowRun.mockResolvedValue({
      groups: [{ reasons: [{ code: "missing_nodes", nodes: ["Foo"] }] }],
    });
    const wrapper = await mountPanel();
    expect(wrapper.find("[role=menu]").exists()).toBe(false);
    expect(wrapper.text()).toContain("can run as they stand");
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
    await first.findAll("[role=menuitemradio]")[0].trigger("click");
    await first.find("button.run").trigger("click");
    await flush();
    first.unmount();
    const second = await mountPanel();
    expect(checkedRow(second)).toBe("Widen, Outpaint");
  });

  it("re-reads the last card once the library list arrives", async () => {
    window.localStorage.setItem("pixlstash:editTabWorkflow:lib-1", "out");
    const wrapper = await mountPanel();
    expect(checkedRow(wrapper)).toBe("Relight, Image to Image");
    useLibrariesStore().libraries = [{ uuid: "lib-1", is_active: true }];
    await flush();
    expect(checkedRow(wrapper)).toBe("Widen, Outpaint");
  });

  it("hands More options the instruction trimmed, blank as empty", async () => {
    const wrapper = await mountPanel();
    const moreOptions = wrapper.findAll("button").find((b) =>
      b.text().includes("More options"),
    );
    await wrapper.find("textarea").setValue("   ");
    await moreOptions.trigger("click");
    await wrapper.find("textarea").setValue("  warmer light ");
    await moreOptions.trigger("click");
    expect(wrapper.emitted("more-options").map(([p]) => p.prompt)).toEqual([
      "",
      "warmer light",
    ]);
  });

  it("keeps a card picked by hand when the library list arrives late", async () => {
    window.localStorage.setItem("pixlstash:editTabWorkflow:lib-1", "i2i");
    const wrapper = await mountPanel();
    await wrapper.findAll("[role=menuitemradio]")[0].trigger("click");
    useLibrariesStore().libraries = [{ uuid: "lib-1", is_active: true }];
    await flush();
    expect(checkedRow(wrapper)).toBe("Widen, Outpaint");
  });

  it("offers Show it only once the result has reached the stack", async () => {
    const pictures = await import("../../api/pictures");
    const stacks = await import("../../api/stacks");
    pictures.getPictureMetadata.mockResolvedValue({ stack_id: 3 });
    stacks.listStackPictures.mockResolvedValue([{ id: 7 }]);
    const wrapper = await mountPanel();
    const showIt = () =>
      wrapper.findAll("button").find((b) => b.text() === "Show it");
    vi.useFakeTimers();
    try {
      await wrapper.find("button.run").trigger("click");
      await vi.advanceTimersByTimeAsync(0);
      await wrapper.setProps({ comfyuiProgress: { status: "completed" } });
      await vi.advanceTimersByTimeAsync(0);
      // Imported a moment after ComfyUI finishes: no link to a missing picture.
      expect(showIt()).toBeUndefined();
      expect(wrapper.text()).toContain("Adding it to the stack");
      stacks.listStackPictures.mockResolvedValue([
        { id: 7 },
        { id: 99, created_at: "2026-01-01T00:00:00Z" },
      ]);
      await vi.advanceTimersByTimeAsync(1500);
      await showIt().trigger("click");
      expect(wrapper.emitted("show-picture")).toEqual([[99, 7]]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so, with no link, when the result never reaches the stack", async () => {
    const pictures = await import("../../api/pictures");
    pictures.getPictureMetadata.mockResolvedValue({ stack_id: 3 });
    const wrapper = await mountPanel();
    vi.useFakeTimers();
    try {
      await wrapper.find("button.run").trigger("click");
      await vi.advanceTimersByTimeAsync(0);
      await wrapper.setProps({ comfyuiProgress: { status: "completed" } });
      await vi.advanceTimersByTimeAsync(60000);
      expect(wrapper.text()).toContain("It has not reached the stack yet");
      expect(wrapper.text()).not.toContain("Show it");
    } finally {
      vi.useRealTimers();
    }
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

  it("tells the lightbox while its run is going", async () => {
    const wrapper = await mountPanel();
    await wrapper.find("button.run").trigger("click");
    await flush();
    await wrapper.setProps({ comfyuiProgress: { status: "completed" } });
    expect(wrapper.emitted("running")).toEqual([[true], [false]]);
  });

  it("keeps the run on the picture it was for", async () => {
    const wrapper = await mountPanel();
    await wrapper.find("textarea").setValue("golden hour");
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(wrapper.text()).toContain("Running");
    await wrapper.setProps({ pictureId: 8 });
    // Another picture: its own form, and the lightbox's bar back for the run.
    expect(wrapper.text()).not.toContain("Running");
    expect(wrapper.emitted("running")).toEqual([[true], [false]]);
    await wrapper.setProps({ comfyuiProgress: { status: "completed" } });
    expect(wrapper.text()).not.toContain("Last edit");
    await wrapper.setProps({ pictureId: 7 });
    expect(wrapper.text()).toContain("Last edit");
    expect(wrapper.text()).toContain("“golden hour”");
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
