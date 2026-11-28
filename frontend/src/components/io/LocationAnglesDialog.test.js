// More angles… (#2819): one shots video from the chosen picture, its frames
// taken out by the server, each restored from its frame with the picture as
// the second reference, checked, and the kept views put in the location's set
// with the video, the frames sent to the Scrapheap.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  buildLocationShotsPrompt: vi.fn(),
  buildSheetPrompt: vi.fn(),
  createPictureSet: vi.fn(),
  listSetInStudio: vi.fn(),
  getWorkflowCard: vi.fn(),
  takeLocationShots: vi.fn(),
  checkLocationShots: vi.fn(),
  bulkAddPicturesToSet: vi.fn(),
  deletePictures: vi.fn(),
  restoreScrapheap: vi.fn(),
  getRunOutcome: vi.fn(),
  listWorkflowCards: vi.fn(),
  preflightWorkflowRun: vi.fn(),
  runWorkflowCard: vi.fn(),
  stopRun: vi.fn(),
  locationPictureThings: vi.fn(),
  listPicturesByIds: vi.fn(),
  embellishPrompt: vi.fn(),
  exportWorkflow: vi.fn(),
  getWorkflowFormInputs: vi.fn(),
}));
vi.mock("../../api/characterSheets", () => ({
  buildLocationShotsPrompt: api.buildLocationShotsPrompt,
  buildSheetPrompt: api.buildSheetPrompt,
  takeLocationShots: api.takeLocationShots,
  checkLocationShots: api.checkLocationShots,
  locationPictureThings: api.locationPictureThings,
  embellishPrompt: api.embellishPrompt,
}));
vi.mock("../../api/pictureSets", () => ({
  bulkAddPicturesToSet: api.bulkAddPicturesToSet,
  createPictureSet: api.createPictureSet,
}));
vi.mock("../../api/studio", () => ({ listSetInStudio: api.listSetInStudio }));
vi.mock("../../api/pictures", () => ({
  deletePictures: api.deletePictures,
  restoreScrapheap: api.restoreScrapheap,
  listPicturesByIds: api.listPicturesByIds,
  pictureMediaUrl: (id, format) => `/media/${id}.${format}`,
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));
vi.mock("../../api/workflows", () => ({
  exportWorkflow: api.exportWorkflow,
  getRunOutcome: api.getRunOutcome,
  getWorkflowFormInputs: api.getWorkflowFormInputs,
  getWorkflowCard: api.getWorkflowCard,
  listWorkflowCards: api.listWorkflowCards,
  preflightWorkflowRun: api.preflightWorkflowRun,
  runWorkflowCard: api.runWorkflowCard,
  stopRun: api.stopRun,
}));

import LocationAnglesDialog from "./LocationAnglesDialog.vue";
import BaseModelInput from "../widgets/BaseModelInput.vue";
import { useLocationDrawStore } from "../../stores/useLocationDrawStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import ZoomPopout from "../widgets/ZoomPopout.vue";

const LOCATION = {
  id: 30,
  name: "The pub",
  description: "a pub",
  picture_ids: [31, 32, 33, 34],
};

const OUTPUTS = { "run-video": [500], "run-r1": [601], "run-r2": [602], "run-r3": [603] };

beforeEach(() => {
  setActivePinia(createPinia());
  for (const fn of Object.values(api)) fn.mockReset();
  api.listWorkflowCards.mockResolvedValue({
    cards: [
      { id: "manual:shots", name: "MiniMax H3 Location Shots (PixlStash)" },
      { id: "manual:restore", name: "MiniMax H3 Restore a Frame (PixlStash)" },
    ],
  });
  api.preflightWorkflowRun.mockImplementation(async ({ workflow_id }) => ({
    groups: [
      {
        reasons: [{ code: "picture_input_unfilled" }],
        picture_inputs:
          workflow_id === "manual:shots"
            ? [{ slot_label: "core:a1", title: "The location", input_name: "image", mode: "picker" }]
            : [
                { slot_label: "core:b2", title: "The original", input_name: "image", mode: "picker" },
                { slot_label: "core:c3", title: "The frame", input_name: "image", mode: "picker" },
              ],
      },
    ],
  }));
  api.stopRun.mockResolvedValue({ stopped: 1 });
  api.getWorkflowFormInputs.mockResolvedValue({
    nodes: [
      // Another primitive first: only the duration's is the video's length.
      { node_id: "105_120", title: "Float (strength)", class_type: "PrimitiveFloat",
        inputs: [{ slot_label: "core:str", input_name: "value", value: 0.5 }] },
      { node_id: "105_111", title: "Float (duration)", class_type: "PrimitiveFloat",
        inputs: [{ slot_label: "core:dur", input_name: "value", value: 8 }] },
      { node_id: "105_91", title: "Create Video", class_type: "CreateVideo",
        inputs: [{ slot_label: "core:fps", input_name: "fps", value: 24 }] },
    ],
  });
  api.locationPictureThings.mockImplementation(async (id) => ({
    things: [`the fireplace of ${id} on the left`],
  }));
  api.listPicturesByIds.mockImplementation(async (ids) => ids.map((id) => ({ id, format: "png" })));
  api.buildLocationShotsPrompt.mockResolvedValue({ prompt: "SHOTS PROMPT", seconds: 8 });
  let restore = 0;
  api.runWorkflowCard.mockImplementation(async ({ workflow_id }) => ({
    prompts: [{ run_id: workflow_id === "manual:shots" ? "run-video" : `run-r${(restore += 1)}` }],
  }));
  api.getRunOutcome.mockImplementation(async (key) => ({
    status: "completed",
    picture_ids: OUTPUTS[key],
  }));
  api.takeLocationShots.mockResolvedValue({ picture_ids: [701, 702, 703] });
  api.checkLocationShots.mockResolvedValue({
    views: [
      { frame_id: 701, restored_id: 601, ok: true, reason: "" },
      { frame_id: 702, restored_id: 602, ok: false, reason: "It turned back into the original picture." },
      { frame_id: 703, restored_id: 603, ok: true, reason: "" },
    ],
  });
});

function mountDialog() {
  return mount(LocationAnglesDialog, { props: { open: true, location: LOCATION } });
}

describe("More angles", () => {
  it("films, takes the frames, restores each from its frame, and keeps what passes", async () => {
    const push = vi.spyOn(useNoticeStore(), "push");
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();

    const [video, ...restores] = api.runWorkflowCard.mock.calls.map(([body]) => body);
    expect(video).toMatchObject({
      workflow_id: "manual:shots",
      prompt: "SHOTS PROMPT",
      inputs: [{ slot_label: "core:a1", input_name: "image", picture_id: 31 }],
    });
    // The video is the location's; only the restores are held until kept.
    expect(video.hold).toBeUndefined();
    expect(api.takeLocationShots).toHaveBeenCalledWith(500, 3);
    expect(restores).toHaveLength(3);
    expect(restores[0]).toMatchObject({ workflow_id: "manual:restore", hold: true });
    // The frame goes in as the frame, the picture as the original, by label.
    expect(restores[0].inputs).toEqual([
      { slot_label: "core:c3", input_name: "image", picture_id: 701 },
      { slot_label: "core:b2", input_name: "image", picture_id: 31 },
    ]);
    expect(api.checkLocationShots).toHaveBeenCalledWith(31, [
      { frame_id: 701, restored_id: 601 },
      { frame_id: 702, restored_id: 602 },
      { frame_id: 703, restored_id: 603 },
    ]);
    expect(wrapper.text()).toContain("turned back into the original");
    // Seen as it ended: no notice besides.
    expect(push).not.toHaveBeenCalled();

    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(api.restoreScrapheap).toHaveBeenCalledWith([601, 603]);
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(30, [500, 601, 603]);
    expect(api.deletePictures).toHaveBeenCalledWith([701, 702, 703]);
    expect(wrapper.emitted("done")[0]).toEqual([2]);
  });

  it("draws a new location's wide pictures first, then films the views from the chosen one", async () => {
    api.listWorkflowCards.mockResolvedValue({
      cards: [
        { id: "manual:wide", name: "MiniMax H3 Image T2I (PixlStash)" },
        { id: "manual:shots", name: "MiniMax H3 Location Shots (PixlStash)" },
        { id: "manual:restore", name: "MiniMax H3 Restore a Frame (PixlStash)" },
      ],
    });
    api.getWorkflowCard.mockResolvedValue({
      card: {
        defaults: [
          { slot_label: "core:w", input_name: "width" },
          { slot_label: "core:h", input_name: "height" },
          { slot_label: "core:s", input_name: "seed" },
        ],
      },
    });
    api.buildSheetPrompt.mockResolvedValue({ prompt: "WIDE", negative: "NEG", width: 2560, height: 1440 });
    const shotRuns = api.runWorkflowCard.getMockImplementation();
    api.runWorkflowCard.mockImplementation(async (body) =>
      body.workflow_id === "manual:wide"
        ? { prompts: [1, 2, 3, 4].map((n) => ({ run_id: `run-w${n}` })) }
        : shotRuns(body),
    );
    Object.assign(OUTPUTS, { "run-w1": [41], "run-w2": [42], "run-w3": [43], "run-w4": [44] });
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 77, name: "Walled garden" } });

    const wrapper = mount(LocationAnglesDialog, { props: { open: true, location: null } });
    expect(wrapper.find('[data-testid="la-draw"]').exists()).toBe(false);
    await wrapper.find('[data-testid="la-name"] input').setValue("Walled garden");
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a walled garden");
    await wrapper.find('[data-testid="la-wide"]').trigger("click");
    await flushPromises();

    expect(api.buildSheetPrompt).toHaveBeenCalledWith({
      kind: "location",
      description: "a walled garden",
      single: true,
    });
    const wide = api.runWorkflowCard.mock.calls[0][0];
    expect(wide).toMatchObject({ workflow_id: "manual:wide", prompt: "WIDE", count: 4, hold: true });
    expect(wide.values).toEqual([
      { slot_label: "core:w", input_name: "width", value: 2560 },
      { slot_label: "core:h", input_name: "height", value: 1440 },
    ]);
    // The four become the pictures to start from; the second is chosen.
    const sources = wrapper.findAll(".la-source");
    expect(sources).toHaveLength(4);
    await sources[1].trigger("click");
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    const video = api.runWorkflowCard.mock.calls[1][0];
    expect(video.inputs[0].picture_id).toBe(42);
    // A run takes only a kept picture: the chosen one is back before it films.
    expect(api.restoreScrapheap.mock.calls[0]).toEqual([[42]]);
    expect(api.restoreScrapheap.mock.invocationCallOrder[0]).toBeLessThan(
      api.runWorkflowCard.mock.invocationCallOrder[1],
    );
    expect(api.buildLocationShotsPrompt.mock.calls[0][0].description).toBe("a walled garden");

    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(api.createPictureSet).toHaveBeenCalledWith({
      name: "Walled garden",
      description: "a walled garden",
    });
    expect(api.listSetInStudio).toHaveBeenCalledWith(77, "location");
    expect(api.restoreScrapheap).toHaveBeenCalledWith([42, 601, 603]);
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(77, [42, 500, 601, 603]);
    expect(wrapper.emitted("done")[0]).toEqual([2, { id: 77, name: "Walled garden" }, [42, 500, 601, 603]]);
  });

  it("retries a failed Use into the set it already made, never a second one", async () => {
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 78, name: "Yard" } });
    api.bulkAddPicturesToSet.mockRejectedValueOnce(new Error("offline"));
    api.listWorkflowCards.mockResolvedValue({
      cards: [
        { id: "manual:wide", name: "MiniMax H3 Image T2I (PixlStash)" },
        { id: "manual:shots", name: "MiniMax H3 Location Shots (PixlStash)" },
        { id: "manual:restore", name: "MiniMax H3 Restore a Frame (PixlStash)" },
      ],
    });
    api.getWorkflowCard.mockResolvedValue({
      card: { defaults: [{ slot_label: "core:w", input_name: "width" }] },
    });
    api.buildSheetPrompt.mockResolvedValue({ prompt: "WIDE", width: 2560, height: 1440 });
    const shotRuns = api.runWorkflowCard.getMockImplementation();
    api.runWorkflowCard.mockImplementation(async (body) =>
      body.workflow_id === "manual:wide" ? { prompts: [{ run_id: "run-w1" }] } : shotRuns(body),
    );
    Object.assign(OUTPUTS, { "run-w1": [41] });
    const wrapper = mount(LocationAnglesDialog, { props: { open: true, location: null } });
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a yard");
    await wrapper.find('[data-testid="la-wide"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();

    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(wrapper.emitted("done")).toBeUndefined();
    // Not listed in the Studio while it is still empty.
    expect(api.listSetInStudio).not.toHaveBeenCalled();
    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(api.createPictureSet).toHaveBeenCalledTimes(1);
    expect(api.bulkAddPicturesToSet).toHaveBeenLastCalledWith(78, [41, 500, 601, 603]);
    expect(api.listSetInStudio).toHaveBeenCalledWith(78, "location");
    expect(wrapper.emitted("done")).toHaveLength(1);
  });

  it("never sends the picture a failed Use was building on back to the Scrapheap", async () => {
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 79, name: "Yard" } });
    api.bulkAddPicturesToSet.mockRejectedValueOnce(new Error("offline"));
    api.deletePictures.mockResolvedValue({});
    api.listWorkflowCards.mockResolvedValue({
      cards: [
        { id: "manual:wide", name: "MiniMax H3 Image T2I (PixlStash)" },
        { id: "manual:shots", name: "MiniMax H3 Location Shots (PixlStash)" },
        { id: "manual:restore", name: "MiniMax H3 Restore a Frame (PixlStash)" },
      ],
    });
    api.getWorkflowCard.mockResolvedValue({ card: { defaults: [{ slot_label: "core:w", input_name: "width" }] } });
    api.buildSheetPrompt.mockResolvedValue({ prompt: "WIDE", width: 2560, height: 1440 });
    const shotRuns = api.runWorkflowCard.getMockImplementation();
    api.runWorkflowCard.mockImplementation(async (body) =>
      body.workflow_id === "manual:wide" ? { prompts: [{ run_id: "run-w1" }] } : shotRuns(body),
    );
    Object.assign(OUTPUTS, { "run-w1": [41] });
    const wrapper = mount(LocationAnglesDialog, { props: { open: true, location: null } });
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a yard");
    await wrapper.find('[data-testid="la-wide"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(wrapper.emitted("done")).toBeUndefined();

    api.deletePictures.mockClear();
    await wrapper.findAll("button").find((b) => b.text() === "Discard").trigger("click");
    await flushPromises();
    expect(api.deletePictures).not.toHaveBeenCalledWith(expect.arrayContaining([41]));
  });

  it("offers what is in the chosen picture as each view's words, read again when the picture changes", async () => {
    const wrapper = mountDialog();
    await flushPromises();
    const offered = () =>
      wrapper.findAllComponents(BaseModelInput).find((c) => c.vm.$attrs["data-testid"] === "la-words-0");
    expect(offered().props("options")).toEqual(["the fireplace of 31 on the left"]);

    await wrapper.findAll(".la-source")[2].trigger("click");
    await flushPromises();
    expect(api.locationPictureThings).toHaveBeenLastCalledWith(33);
    expect(offered().props("options")).toEqual(["the fireplace of 33 on the left"]);

    // Back to a picture already read: its things again, not asked twice.
    await wrapper.findAll(".la-source")[0].trigger("click");
    await flushPromises();
    expect(offered().props("options")).toEqual(["the fireplace of 31 on the left"]);
    expect(api.locationPictureThings).toHaveBeenCalledTimes(2);

    const light = wrapper
      .findAllComponents(BaseModelInput)
      .find((c) => c.vm.$attrs["data-testid"] === "la-light");
    expect(light.props("options")).toContain("candlelight");
  });

  it("zooms a picture full size and can start from it there", async () => {
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.findAll('[data-testid="la-zoom-open"]')[3].trigger("click");
    await flushPromises();
    const zoom = wrapper.findComponent(ZoomPopout);
    expect(zoom.props("open")).toBe(true);
    expect(zoom.props("src")).toBe("/media/34.png");

    // The pop-out is a layer in <body>.
    document.body.querySelector('[data-testid="la-zoom-choose"]').click();
    zoom.vm.$emit("close");
    await flushPromises();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    expect(api.runWorkflowCard.mock.calls[0][0].inputs[0].picture_id).toBe(34);
  });

  it("sends a wide picture drawn from back to the Scrapheap when the new location is discarded", async () => {
    api.listWorkflowCards.mockResolvedValue({
      cards: [
        { id: "manual:wide", name: "MiniMax H3 Image T2I (PixlStash)" },
        { id: "manual:shots", name: "MiniMax H3 Location Shots (PixlStash)" },
        { id: "manual:restore", name: "MiniMax H3 Restore a Frame (PixlStash)" },
      ],
    });
    api.getWorkflowCard.mockResolvedValue({ card: { defaults: [{ slot_label: "core:w", input_name: "width" }] } });
    api.buildSheetPrompt.mockResolvedValue({ prompt: "WIDE", width: 2560, height: 1440 });
    const shotRuns = api.runWorkflowCard.getMockImplementation();
    api.runWorkflowCard.mockImplementation(async (body) =>
      body.workflow_id === "manual:wide"
        ? { prompts: [{ run_id: "run-w1" }, { run_id: "run-w2" }] }
        : shotRuns(body),
    );
    Object.assign(OUTPUTS, { "run-w1": [41], "run-w2": [42] });
    api.deletePictures.mockResolvedValue({});
    const wrapper = mount(LocationAnglesDialog, { props: { open: true, location: null } });
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a yard");
    await wrapper.find('[data-testid="la-wide"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    expect(api.restoreScrapheap).toHaveBeenCalledWith([41]);
    api.deletePictures.mockClear();

    await wrapper.findAll("button").find((b) => b.text() === "Discard").trigger("click");
    await flushPromises();
    expect(api.deletePictures).toHaveBeenCalledWith([41]);
  });

  /** A new location's dialog with the wide workflow ready; `llmType` is its
   * text encoder loader's type. */
  function newLocation({ llmType = "minimax" } = {}) {
    api.listWorkflowCards.mockResolvedValue({
      cards: [
        { id: "manual:wide", name: "MiniMax H3 Image T2I (PixlStash)" },
        { id: "manual:shots", name: "MiniMax H3 Location Shots (PixlStash)" },
        { id: "manual:restore", name: "MiniMax H3 Restore a Frame (PixlStash)" },
      ],
    });
    api.exportWorkflow.mockResolvedValue({
      workflow: {
        4: { class_type: "CLIPLoader", inputs: { clip_name: "qwen3vl.safetensors", type: llmType, junk: "x" } },
        5: { class_type: "VAELoader", inputs: { vae_name: "vae.safetensors" } },
      },
    });
    api.getWorkflowCard.mockResolvedValue({
      card: { defaults: [{ slot_label: "core:w", input_name: "width" }] },
    });
    api.buildSheetPrompt.mockResolvedValue({ prompt: "WIDE", negative: "NEG", width: 2560, height: 1440 });
    const shotRuns = api.runWorkflowCard.getMockImplementation();
    let round = 0;
    api.runWorkflowCard.mockImplementation(async (body) => {
      if (body.workflow_id !== "manual:wide") return shotRuns(body);
      round += 1;
      return { prompts: [1, 2].map((n) => ({ run_id: `run-w${round}-${n}` })) };
    });
    Object.assign(OUTPUTS, {
      "run-w1-1": [41], "run-w1-2": [42], "run-w2-1": [51], "run-w2-2": [52],
    });
    return mount(LocationAnglesDialog, { props: { open: true, location: null } });
  }

  it("shows the wide pictures' prompt to change, and draws with the changed one", async () => {
    const wrapper = newLocation();
    await flushPromises();
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a pub");
    await wrapper.find('[data-testid="la-write"]').trigger("click");
    await flushPromises();
    const field = wrapper.find('[data-testid="la-prompt"] textarea');
    expect(field.element.value).toBe("WIDE");
    await field.setValue("WIDE, with green leather booths");
    await wrapper.find('[data-testid="la-wide"]').trigger("click");
    await flushPromises();
    expect(api.runWorkflowCard.mock.calls[0][0].prompt).toBe("WIDE, with green leather booths");
  });

  it("draws four new wide pictures in place of the four shown, from the prompt as changed", async () => {
    const wrapper = newLocation();
    await flushPromises();
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a pub");
    await wrapper.find('[data-testid="la-wide"]').trigger("click");
    await flushPromises();
    const ids = () => wrapper.findAll(".la-source img").map((i) => i.attributes("src"));
    expect(ids()).toEqual(["/thumb/41.webp", "/thumb/42.webp"]);
    // Two of the four asked for: said, not left to be noticed.
    expect(wrapper.find('[data-testid="la-wide-short"]').text()).toContain("2 of the 4");
    // The prompt it drew with is the one shown.
    const again = wrapper.find('[data-testid="la-prompt-again"] textarea');
    expect(again.element.value).toBe("WIDE");
    await again.setValue("WIDE at dusk");
    await wrapper.find('[data-testid="la-wide-again"]').trigger("click");
    await flushPromises();
    expect(api.runWorkflowCard.mock.calls[1][0]).toMatchObject({ prompt: "WIDE at dusk", count: 4, hold: true });
    expect(ids()).toEqual(["/thumb/51.webp", "/thumb/52.webp"]);
    expect(wrapper.find('[data-testid="la-source-0"]').attributes("aria-checked")).toBe("true");
  });

  it("embellishes the prompt with the LLM the wide workflow encodes with, and only with one", async () => {
    api.embellishPrompt.mockResolvedValue({ prompt: "A richer pub." });
    const wrapper = newLocation();
    await flushPromises();
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a pub");
    await wrapper.find('[data-testid="la-embellish"]').trigger("click");
    await flushPromises();
    // Nothing written yet: the place's prompt first, then its rewrite.
    expect(api.embellishPrompt).toHaveBeenCalledWith(
      { class_type: "CLIPLoader", inputs: { clip_name: "qwen3vl.safetensors", type: "minimax" } },
      "WIDE",
    );
    expect(wrapper.find('[data-testid="la-prompt"] textarea').element.value).toBe("A richer pub.");
    wrapper.unmount();

    const plain = newLocation({ llmType: "sd3" });
    await flushPromises();
    expect(plain.find('[data-testid="la-embellish"]').exists()).toBe(false);
  });

  it("shows the picture it started from beside the views, and zooms through them from it", async () => {
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    const original = wrapper.find('[data-testid="la-original"]');
    expect(original.find("img").attributes("src")).toBe("/thumb/31.webp");
    expect(original.text()).toContain("Started from");

    await original.find('[data-testid="la-zoom-open"]').trigger("click");
    await flushPromises();
    const zoom = wrapper.findComponent(ZoomPopout);
    expect(zoom.props("caption")).toBe("The picture you started from");
    expect(zoom.props("hasNext")).toBe(true);
    zoom.vm.$emit("next");
    await flushPromises();
    expect(zoom.props("src")).toBe("/media/601.png");
    // Kept from the zoom, without leaving it.
    document.body.querySelector('[data-testid="la-zoom-keep"]').click();
    await flushPromises();
    zoom.vm.$emit("close");
    await flushPromises();
    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(30, [500, 603]);
  });

  it("asks for as many new views as picked, in a video two seconds longer a view", async () => {
    api.buildLocationShotsPrompt.mockResolvedValue({ prompt: "SHOTS PROMPT", seconds: 10 });
    api.takeLocationShots.mockResolvedValue({ picture_ids: [701, 702, 703, 704] });
    const wrapper = mountDialog();
    const count = wrapper.findComponent('[data-testid="la-view-count"]');
    const presets = () =>
      wrapper.findAllComponents({ name: "AppSelect" })
        .filter((c) => /la-preset-/.test(c.attributes("data-testid") ?? ""))
        .map((c) => c.props("modelValue"));
    expect(presets()).toEqual(["closer", "opposite", "reverse"]);
    expect(wrapper.text()).toContain("8 s of video");

    // One row: the first keeps its words; then four: a fourth preset is added.
    await wrapper.findAll(".la-shot input.tbm-input")[0].setValue("the fireplace");
    await count.vm.$emit("update:modelValue", 1);
    expect(presets()).toEqual(["closer"]);
    await count.vm.$emit("update:modelValue", 4);
    expect(presets()).toEqual(["closer", "opposite", "reverse", "left"]);
    expect(wrapper.text()).toContain("10 s of video");

    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    const asked = api.buildLocationShotsPrompt.mock.calls[0][0].shots;
    expect(asked).toHaveLength(4);
    expect(asked[0].words).toBe("the fireplace");
    const video = api.runWorkflowCard.mock.calls[0][0];
    expect(video.values).toEqual([{ slot_label: "core:dur", input_name: "value", value: 10 }]);
    expect(api.takeLocationShots).toHaveBeenCalledWith(500, 4);
  });

  it("films at the workflow's own length when it has no duration to set", async () => {
    api.getWorkflowFormInputs.mockResolvedValue({ nodes: [] });
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    expect(api.runWorkflowCard.mock.calls[0][0].values).toBeUndefined();
  });

  it("sends the views a failed Use brought back to the Scrapheap when it is discarded", async () => {
    api.bulkAddPicturesToSet.mockRejectedValueOnce(new Error("offline"));
    api.deletePictures.mockResolvedValue({});
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(api.restoreScrapheap).toHaveBeenCalledWith([601, 603]);
    expect(wrapper.emitted("done")).toBeUndefined();

    await wrapper.findAll("button").find((b) => b.text() === "Discard").trigger("click");
    await flushPromises();
    expect(api.deletePictures).toHaveBeenCalledWith([601, 603]);
  });

  it("offers the 2×2 sheet instead of a new location's video", async () => {
    const wrapper = mount(LocationAnglesDialog, { props: { open: true, location: null } });
    await wrapper.find('[data-testid="la-sheet"]').trigger("click");
    expect(wrapper.emitted("sheet")).toHaveLength(1);
  });

  it("keeps the views a video with too few shots made, numbered rather than misnamed", async () => {
    api.takeLocationShots.mockResolvedValue({ picture_ids: [701, 702] });
    api.checkLocationShots.mockResolvedValue({
      views: [
        { frame_id: 701, restored_id: 601, ok: true, reason: "" },
        { frame_id: 702, restored_id: 602, ok: true, reason: "" },
      ],
    });
    const wrapper = mountDialog();
    expect(wrapper.find('[data-testid="la-count"]').exists()).toBe(false);
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();

    expect(api.runWorkflowCard.mock.calls).toHaveLength(3); // the video and two restores
    expect(wrapper.find('[data-testid="la-count"]').text()).toContain("2 of the 3 views");
    const names = wrapper.findAll(".la-view:not(.la-view--original) .la-view__foot").map((foot) => foot.text());
    expect(names).toEqual(["View 1", "View 2"]);
  });

  it("keeps Discard out of reach while the kept views are being put away", async () => {
    let finish;
    api.bulkAddPicturesToSet.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    const discard = () => wrapper.findAll("button").find((b) => b.text() === "Discard");
    expect(discard().attributes("disabled")).toBeDefined();
    finish({});
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("stops with a reason when a restore run makes nothing", async () => {
    api.getRunOutcome.mockImplementation(async (key) => ({
      status: "completed",
      picture_ids: key === "run-r2" ? [] : OUTPUTS[key],
    }));
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    expect(api.checkLocationShots).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("A restore run made nothing");
  });

  it("rides out one lost answer while following its runs", async () => {
    vi.useFakeTimers();
    const outcome = api.getRunOutcome.getMockImplementation();
    api.getRunOutcome.mockRejectedValueOnce(new Error("network")).mockImplementation(outcome);
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await vi.advanceTimersByTimeAsync(20000);
    vi.useRealTimers();
    expect(api.stopRun).not.toHaveBeenCalled();
    expect(api.checkLocationShots).toHaveBeenCalled();
  });

  it("rides out a lost answer that hits every restore in one pass", async () => {
    vi.useFakeTimers();
    const outcome = api.getRunOutcome.getMockImplementation();
    const missed = new Set();
    api.getRunOutcome.mockImplementation(async (key) => {
      if (key.startsWith("run-r") && !missed.has(key)) {
        missed.add(key);
        throw new Error("network");
      }
      return outcome(key);
    });
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await vi.advanceTimersByTimeAsync(20000);
    vi.useRealTimers();
    expect(missed.size).toBe(3);
    expect(api.stopRun).not.toHaveBeenCalled();
    expect(api.checkLocationShots).toHaveBeenCalled();
  });

  it("stops the other restores when one fails", async () => {
    api.getRunOutcome.mockImplementation(async (key) =>
      key === "run-r1"
        ? { status: "failed", message: "Out of memory." }
        : { status: key === "run-video" ? "completed" : "running", picture_ids: OUTPUTS[key] },
    );
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Out of memory.");
    expect(api.stopRun.mock.calls.map(([key]) => key).sort()).toEqual([
      "run-r1",
      "run-r2",
      "run-r3",
    ]);
  });

  it("says so when a built-in it draws with is gone, and runs nothing", async () => {
    api.listWorkflowCards.mockResolvedValue({
      cards: [{ id: "manual:shots", name: "MiniMax H3 Location Shots (PixlStash)" }],
    });
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await flushPromises();
    expect(api.runWorkflowCard).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Restore a Frame (PixlStash) is not in Workflows");
  });

  it("goes on in the background when closed mid-draw, and shows how far it has got", async () => {
    let videoDone = false;
    api.getRunOutcome.mockImplementation(async (key) =>
      key === "run-video" && !videoDone
        ? { status: "running", picture_ids: [] }
        : { status: "completed", picture_ids: OUTPUTS[key] },
    );
    const draws = useLocationDrawStore();
    const push = vi.spyOn(useNoticeStore(), "push");
    // Only the polls' waits, not the clock: Vue drops an event stamped before
    // its handler was attached, and a button drawn under an advanced fake
    // clock would ignore every click once real time is back.
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await vi.advanceTimersByTimeAsync(10);
    expect(draws.job).toMatchObject({ locationId: 30, name: "The pub", status: "Filming the place…" });
    await wrapper.find('[data-testid="la-background"]').trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    await wrapper.setProps({ open: false });
    // The video finishes with the dialog shut: the draw takes the frames on.
    videoDone = true;
    await vi.advanceTimersByTimeAsync(10000);
    vi.useRealTimers();
    await flushPromises();
    expect(api.stopRun).not.toHaveBeenCalled();
    expect(api.takeLocationShots).toHaveBeenCalledWith(500, 3);
    expect(draws.job).toMatchObject({ locationId: 30, ready: true, fraction: 1 });
    expect(push).toHaveBeenCalledWith(
      expect.objectContaining({ text: "The new views of The pub are ready to pick." }),
    );
    // Opened again, it is where the draw got to, not a fresh start.
    await wrapper.setProps({ open: true });
    expect(wrapper.find('[data-testid="la-use"]').exists()).toBe(true);
    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(draws.job).toBeNull();
  });

  it("drops a draw when the session changes, stopping its runs and touching no picture", async () => {
    api.getRunOutcome.mockResolvedValue({ status: "running", picture_ids: [] });
    const draws = useLocationDrawStore();
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await vi.advanceTimersByTimeAsync(10);
    draws.reset();
    api.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [500] });
    await vi.advanceTimersByTimeAsync(10000);
    vi.useRealTimers();
    expect(api.stopRun).toHaveBeenCalledWith("run-video");
    expect(api.takeLocationShots).not.toHaveBeenCalled();
    // Another library's ids: never sent anywhere from this one.
    expect(api.deletePictures).not.toHaveBeenCalled();
    expect(draws.job).toBeNull();
  });

  it("leaves no draw behind a session change while wide pictures wait to be chosen", async () => {
    const wrapper = newLocation();
    await flushPromises();
    await wrapper.find('[data-testid="la-description"] textarea').setValue("a pub");
    await wrapper.find('[data-testid="la-wide"]').trigger("click");
    await flushPromises();
    const draws = useLocationDrawStore();
    expect(draws.job).toMatchObject({ locationId: null, ready: true });
    draws.reset();
    await flushPromises();
    expect(draws.job).toBeNull();
    expect(api.deletePictures).not.toHaveBeenCalled();
  });

  it("stops following its runs when stopped mid-draw", async () => {
    api.getRunOutcome.mockResolvedValue({ status: "running", picture_ids: [] });
    const draws = useLocationDrawStore();
    vi.useFakeTimers();
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await vi.advanceTimersByTimeAsync(10);
    await wrapper.find('[data-testid="la-stop"]').trigger("click");
    // The video then finishes: a draw still following it would take frames.
    api.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [500] });
    await vi.advanceTimersByTimeAsync(10000);
    vi.useRealTimers();
    expect(api.stopRun).toHaveBeenCalledWith("run-video");
    expect(api.takeLocationShots).not.toHaveBeenCalled();
    expect(draws.job).toBeNull();
    expect(wrapper.find('[data-testid="la-draw"]').exists()).toBe(true);
    expect(wrapper.find(".la-error").exists()).toBe(false);
  });
});
