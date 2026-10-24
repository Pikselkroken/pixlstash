// The lightbox Edit tab's body (#1381): which workflows it lists, what Run
// sends, and that the lightbox stays on the original afterwards.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { markRaw, reactive, ref } from "vue";

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
import { useTasksStore } from "../../stores/useTasksStore";

enableAutoUnmount(afterEach);

const CARDS = [
  { id: "t2i", name: "Portrait", type: "txt2img", type_label: "Text to Image" },
  { id: "up", name: "4x", type: "upscale", type_label: "Upscale" },
  { id: "out", name: "Widen", type: "outpaint", type_label: "Outpaint" },
  { id: "i2i", name: "Relight", type: "img2img", type_label: "Image to Image" },
];

const checkedRow = (wrapper) =>
  wrapper.find("[role=menuitemradio][aria-checked=true]").text();

const flush = () => new Promise((r) => setTimeout(r, 0));

async function mountPanel(extra = {}) {
  const wrapper = mount(OverlayEditPanel, {
    props: { pictureId: 7, active: true, ...extra },
    global: {
      stubs: {
        AppButton: { template: "<button class='run' @click=\"$emit('click')\"><slot /></button>" },
        "v-icon": true,
        Tooltip: true,
        ModelDownloadButton: {
          props: ["file"],
          template: "<i class='dl'>{{ file }}</i>",
        },
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
      query: { workflow: "out" },
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
    // A missing model alongside another reason: a download would not fix it.
    preflightWorkflowRun.mockImplementation(async ({ target }) => ({
      groups: [
        {
          reasons:
            target === "i2i"
              ? [
                  { code: "missing_models", models: [{ file: "a.safetensors" }] },
                  { code: "missing_nodes", nodes: ["Foo"] },
                ]
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

  it("lists a card missing only models, flagged, with Run held", async () => {
    preflightWorkflowRun.mockImplementation(async ({ target }) => ({
      groups: [
        {
          reasons:
            target === "out"
              ? [
                  {
                    code: "missing_models",
                    models: [
                      { file: "fill.safetensors", folder: "unet", downloadable: true },
                      { file: "mine.safetensors", folder: "vae" },
                    ],
                  },
                ]
              : [],
        },
      ],
    }));
    const wrapper = await mountPanel();
    const rows = wrapper.findAll("[role=menuitemradio]");
    expect(rows.map((row) => row.text())).toEqual([
      "Widen, 2 models missing, Outpaint",
      "Relight, Image to Image",
    ]);
    // Not the default while a runnable card is there.
    expect(checkedRow(wrapper)).toBe("Relight, Image to Image");
    expect(wrapper.find(".edit-missing-notice").exists()).toBe(false);

    await rows[0].trigger("click");
    const notice = wrapper.find(".edit-missing-notice");
    expect(notice.text()).toContain("2 models are missing.");
    // A download only where the server can fetch the file.
    expect(notice.findAll(".dl").map((dl) => dl.text())).toEqual([
      "fill.safetensors",
    ]);
    expect(wrapper.find("button.run").attributes("disabled")).toBeDefined();
    // Nor through Ctrl+Enter, which calls submit directly.
    await wrapper.vm.submit();
    expect(runWorkflowCard).not.toHaveBeenCalled();
  });

  it("asks again when a download lands, and turns Run on", async () => {
    let present = false;
    preflightWorkflowRun.mockImplementation(async ({ target }) => ({
      groups: [
        {
          reasons:
            target === "i2i" && !present
              ? [{ code: "missing_models", models: [{ file: "a.safetensors" }] }]
              : [],
        },
      ],
    }));
    listWorkflowCards.mockResolvedValue({ cards: [CARDS[3]] });
    const wrapper = await mountPanel();
    // The only card, so chosen though it cannot run yet.
    expect(checkedRow(wrapper)).toContain("1 model missing");
    expect(wrapper.find("button.run").attributes("disabled")).toBeDefined();

    present = true;
    useTasksStore().downloadsLanded += 1;
    await flush();
    await flush();
    expect(wrapper.find(".edit-missing-notice").exists()).toBe(false);
    expect(checkedRow(wrapper)).toBe("Relight, Image to Image");
    expect(wrapper.find("button.run").attributes("disabled")).toBeUndefined();
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard).toHaveBeenCalled();
  });

  it("sends a card missing only a picture straight to the Run popup", async () => {
    preflightWorkflowRun.mockImplementation(async ({ target }) => ({
      groups: [
        {
          reasons:
            target === "i2i"
              ? [{ code: "picture_input_unfilled", inputs: [] }]
              : [],
        },
      ],
    }));
    window.localStorage.setItem("pixlstash:editTabWorkflow:", "i2i");
    const wrapper = await mountPanel();
    const rows = wrapper.findAll("[role=menuitemradio]");
    expect(rows.map((row) => row.text())).toEqual([
      "Widen, Outpaint",
      "Relight…, Image to Image",
    ]);
    // Never the tab's own card, even when it was the last one used.
    expect(checkedRow(wrapper)).toBe("Widen, Outpaint");
    await wrapper.find("textarea").setValue("warmer light");
    // The tab's own stack choice goes with it, explicit false included.
    await wrapper.find("input[type=checkbox]").setValue(false);
    await rows[1].trigger("click");
    await flush();
    expect(runWorkflowCard).not.toHaveBeenCalled();
    // As an edit of this picture: the popup reads none of its recipe.
    expect(wrapper.emitted("more-options")).toEqual([
      [
        {
          pictureId: 7,
          workflowId: "i2i",
          prompt: "warmer light",
          edit: true,
          stack: false,
        },
      ],
    ]);
    expect(checkedRow(wrapper)).toBe("Widen, Outpaint");
    // More options… still opens the tab's own card, not the click event.
    const moreOptions = wrapper.findAll("button").find((b) =>
      b.text().includes("More options"),
    );
    await moreOptions.trigger("click");
    expect(wrapper.emitted("more-options").at(-1)[0]).toEqual(
      expect.objectContaining({ workflowId: "out", edit: false }),
    );
  });

  it("follows a run the Run popup started for it, as one of its own", async () => {
    const wrapper = await mountPanel();
    useRunDialogStore().editRun = {
      prompts: [{ prompt_id: "p9" }],
      pictureId: 7,
      workflowName: "Relight with reference",
      instruction: "warmer light",
      stack: true,
    };
    await flush();
    await flush();
    expect(wrapper.text()).toContain("Running");
    expect(wrapper.emitted("running")?.at(-1)).toEqual([true]);
    // Taken off the store once followed.
    expect(useRunDialogStore().editRun).toBe(null);
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
        // The workflow, which is what the Tasks tab calls the run.
        label: "Relight",
      }),
    );
    // No source picture: that is what would make the grid step the lightbox
    // to the output, and this design stays on the original.
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

  it("tells the popup's result by the stack the popup read before queuing", async () => {
    const stacks = await import("../../api/stacks");
    const pictures = await import("../../api/pictures");
    pictures.getPictureMetadata.mockResolvedValue({ stack_id: 3 });
    // The output is already in by the time the tab hears of the run.
    stacks.listStackPictures.mockResolvedValue([
      { id: 7 },
      { id: 99, created_at: "2026-01-01T00:00:00Z" },
    ]);
    const wrapper = await mountPanel();
    useRunDialogStore().editRun = {
      prompts: [{ prompt_id: "p9" }],
      pictureId: 7,
      workflowName: "Relight with reference",
      instruction: "",
      stack: true,
      beforeIds: new Set(["7"]),
    };
    await flush();
    await wrapper.setProps({ comfyuiProgress: { status: "completed" } });
    await flush();
    await flush();
    const showIt = wrapper.findAll("button").find((b) => b.text() === "Show it");
    await showIt.trigger("click");
    expect(wrapper.emitted("show-picture")).toEqual([[99, 7]]);
  });

  it("points at no result when the popup could not read the stack first", async () => {
    const stacks = await import("../../api/stacks");
    const pictures = await import("../../api/pictures");
    pictures.getPictureMetadata.mockResolvedValue({ stack_id: 3 });
    // An older member: with the before-run stack unknown it could pass for
    // the result, so nothing is offered.
    stacks.listStackPictures.mockResolvedValue([
      { id: 7 },
      { id: 50, created_at: "2025-01-01T00:00:00Z" },
    ]);
    const wrapper = await mountPanel();
    useRunDialogStore().editRun = {
      prompts: [{ prompt_id: "p9" }],
      pictureId: 7,
      workflowName: "Relight with reference",
      instruction: "",
      stack: true,
      beforeIds: null,
    };
    await flush();
    await wrapper.setProps({ comfyuiProgress: { status: "completed" } });
    await flush();
    await flush();
    expect(wrapper.findAll("button").some((b) => b.text() === "Show it")).toBe(false);
    expect(wrapper.text()).toContain("It has not reached the stack yet");
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

  it("claims the runner's complete tail, but not a later run's", async () => {
    const wrapper = await mountPanel();
    await wrapper.find("button.run").trigger("click");
    await flush();
    await wrapper.setProps({
      comfyuiProgress: { visible: true, status: "completed" },
    });
    // "ComfyUI complete" is this run's, and Last edit already says it.
    expect(wrapper.emitted("running")).toEqual([[true]]);
    await wrapper.setProps({
      comfyuiProgress: { visible: false, status: "idle" },
    });
    expect(wrapper.emitted("running")).toEqual([[true], [false]]);
    // A run from the grid's menus: the lightbox bar is its to show.
    await wrapper.setProps({
      comfyuiProgress: { visible: true, status: "running" },
    });
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

// The lightbox's Paint selection, as `usePaintSelection` hands it over. Raw,
// as the lightbox's own is: mount() makes its props reactive, which would
// unwrap the refs inside.
function fakePaint(steps = []) {
  const stepsRef = ref(steps);
  return markRaw({
    hasSelection: ref(steps.length > 0),
    selection: ref({ version: 1, steps, refine: { grow: 0, feather: 0, fill_holes: 0 } }),
    steps: stepsRef,
    showMask: ref(false),
    maskError: ref(""),
    refine: reactive({ grow: 0, feather: 0, fill_holes: 0 }),
    toggleMask: vi.fn(),
    setRefine: vi.fn(),
    clear: vi.fn(),
  });
}

const RECT = { tool: "rect", mode: "new", box: [0, 0, 0.5, 1] };
const INPAINT = { id: "fill", name: "Fill", type: "inpaint", type_label: "Inpaint" };

describe("the Edit tab with the Paint tools", () => {
  it("sends the selection with the run, on an inpaint workflow", async () => {
    listWorkflowCards.mockResolvedValue({ cards: [...CARDS, INPAINT] });
    const paint = fakePaint();
    const wrapper = await mountPanel({ paint });
    // An Image to Image card first, as without a selection.
    expect(checkedRow(wrapper)).toBe("Relight, Image to Image");
    paint.hasSelection.value = true;
    paint.selection.value = { ...paint.selection.value, steps: [RECT] };
    await flush();
    // The first selection moves the tab onto a workflow that reads a mask.
    expect(checkedRow(wrapper)).toBe("Fill, Inpaint");
    expect(wrapper.text()).toContain("Refine selection");
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({
        picture_ids: [7],
        target: "fill",
        selection: expect.objectContaining({ steps: [RECT] }),
      }),
    );
  });

  it("puts Inpaint first for a selection, over a hand choice, and goes back after", async () => {
    listWorkflowCards.mockResolvedValue({ cards: [...CARDS, INPAINT] });
    const paint = fakePaint();
    const wrapper = await mountPanel({ paint });
    const rows = () => wrapper.findAll("[role=menuitemradio]").map((row) => row.text());
    const out = wrapper
      .findAll("[role=menuitemradio]")
      .find((row) => row.text().includes("Widen"));
    await out.trigger("click");
    expect(rows()[0]).toBe("Widen, Outpaint");
    expect(wrapper.text()).toContain("What should change?");
    paint.hasSelection.value = true;
    await flush();
    expect(checkedRow(wrapper)).toBe("Fill, Inpaint");
    expect(rows()[0]).toBe("Fill, Inpaint");
    // The prompt box asks for what goes in the selection, and says how.
    expect(wrapper.text()).toContain("What goes there?");
    expect(wrapper.find("textarea").attributes("placeholder")).toContain("scarf");
    paint.hasSelection.value = false;
    await flush();
    expect(checkedRow(wrapper)).toBe("Widen, Outpaint");
    expect(rows()[0]).toBe("Widen, Outpaint");
  });

  it("keeps a card chosen while there is a selection, and the last one run on one", async () => {
    const other = { id: "fill2", name: "Patch", type: "inpaint", type_label: "Inpaint" };
    listWorkflowCards.mockResolvedValue({ cards: [...CARDS, INPAINT, other] });
    const paint = fakePaint();
    const wrapper = await mountPanel({ paint });
    paint.hasSelection.value = true;
    await flush();
    expect(checkedRow(wrapper)).toBe("Fill, Inpaint");
    const patch = wrapper
      .findAll("[role=menuitemradio]")
      .find((row) => row.text().includes("Patch"));
    await patch.trigger("click");
    await wrapper.find("button.run").trigger("click");
    await flush();
    // Chosen by hand: clearing the selection leaves it be.
    paint.hasSelection.value = false;
    await flush();
    expect(checkedRow(wrapper)).toBe("Patch, Inpaint");
    const relight = wrapper
      .findAll("[role=menuitemradio]")
      .find((row) => row.text().includes("Relight"));
    await relight.trigger("click");
    paint.hasSelection.value = true;
    await flush();
    // The next selection goes to the inpaint workflow last run on one.
    expect(checkedRow(wrapper)).toBe("Patch, Inpaint");
  });

  it("starts on an Inpaint card when the selection was drawn before the tab loaded", async () => {
    listWorkflowCards.mockResolvedValue({ cards: [...CARDS, INPAINT] });
    const wrapper = await mountPanel({ paint: fakePaint([RECT]) });
    expect(checkedRow(wrapper)).toBe("Fill, Inpaint");
  });

  it("keeps a card chosen with a selection up when the library list arrives late", async () => {
    listWorkflowCards.mockResolvedValue({ cards: [...CARDS, INPAINT] });
    const wrapper = await mountPanel({ paint: fakePaint([RECT]) });
    expect(checkedRow(wrapper)).toBe("Fill, Inpaint");
    const relight = wrapper
      .findAll("[role=menuitemradio]")
      .find((row) => row.text().includes("Relight"));
    await relight.trigger("click");
    useLibrariesStore().libraries = [{ uuid: "lib-1", is_active: true }];
    await flush();
    expect(checkedRow(wrapper)).toBe("Relight, Image to Image");
  });

  it("shows how to write the prompt on request", async () => {
    const wrapper = await mountPanel();
    const toggle = wrapper
      .findAll("button")
      .find((b) => b.text() === "How to write it");
    const help = wrapper.find(".edit-help");
    expect(toggle.attributes("aria-controls")).toBe(help.attributes("id"));
    // `v-show`: not attached to a document, so read the style it sets.
    expect(help.element.style.display).toBe("none");
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("true");
    expect(help.element.style.display).toBe("");
    expect(help.text()).toContain("make the sky stormy");
  });

  it("warns, and does not send a mask, where the workflow has none", async () => {
    const paint = fakePaint();
    const wrapper = await mountPanel({ paint });
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("selection");
    expect(wrapper.text()).not.toContain("do not use a mask");
    paint.hasSelection.value = true;
    paint.selection.value = { ...paint.selection.value, steps: [RECT] };
    await flush();
    // No inpaint card to move to: it stays, and says what the run would do.
    expect(checkedRow(wrapper)).toBe("Relight, Image to Image");
    expect(wrapper.text()).toContain("do not use a mask");
  });

  it("runs on every picture Edit… named, unless a hand selection holds it", async () => {
    const paint = fakePaint();
    const wrapper = await mountPanel({ paint, applyToIds: [7, 8, 9] });
    expect(wrapper.find("button.run").text()).toBe("Run on 3 pictures");
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard.mock.calls[0][0].picture_ids).toEqual([7, 8, 9]);
    runWorkflowCard.mockClear();
    paint.hasSelection.value = true;
    paint.selection.value = { ...paint.selection.value, steps: [RECT] };
    await flush();
    expect(wrapper.find("button.run").text()).toBe("Run edit");
    expect(wrapper.text()).toContain("A hand selection stays on the picture");
    // The track says where the run goes: this picture, not the greyed-out all.
    expect(wrapper.find("[role=radio][aria-checked=true]").text()).toBe("This picture");
    await wrapper.find("button.run").trigger("click");
    await flush();
    expect(runWorkflowCard.mock.calls[0][0].picture_ids).toEqual([7]);
  });

  it("hands the selection to the Run popup through More options…", async () => {
    const wrapper = await mountPanel({ paint: fakePaint([RECT]) });
    const more = wrapper.findAll("button").find((b) => b.text() === "More options…");
    await more.trigger("click");
    expect(wrapper.emitted("more-options")[0][0].selection).toMatchObject({
      steps: [RECT],
    });
  });
});
