// The sheet step drawing an item (an outfit here) rather than a character:
// prose workflows only, MiniMax H3 Image first and a shipped one ahead of the
// owner's own; a form of description, name and count alone; its sheets rank
// without faces; and using one makes a set of its panels under the person.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  buildSheetPrompt: vi.fn(),
  rankSheets: vi.fn(),
  useSheets: vi.fn(),
  rankItemSheets: vi.fn(),
  useItemSheets: vi.fn(),
  createPictureSet: vi.fn(),
  bulkAddPicturesToSet: vi.fn(),
  deletePictureSet: vi.fn(),
  getCharacter: vi.fn(),
  getReferencePictures: vi.fn(),
  patchCharacter: vi.fn(),
  listAdapters: vi.fn(),
  getRunOutcome: vi.fn(),
  getWorkflowCard: vi.fn(),
  listWorkflowCards: vi.fn(),
  preflightWorkflowRun: vi.fn(),
  runWorkflowCard: vi.fn(),
  sheetCopyWorkflow: vi.fn(),
}));
vi.mock("../../api/characterSheets", () => ({
  buildSheetPrompt: api.buildSheetPrompt,
  rankSheets: api.rankSheets,
  useSheets: api.useSheets,
  rankItemSheets: api.rankItemSheets,
  useItemSheets: api.useItemSheets,
}));
vi.mock("../../api/pictureSets", () => ({
  createPictureSet: api.createPictureSet,
  bulkAddPicturesToSet: api.bulkAddPicturesToSet,
  deletePictureSet: api.deletePictureSet,
}));
vi.mock("../../api/characters", () => ({
  getCharacter: api.getCharacter,
  getReferencePictures: api.getReferencePictures,
  patchCharacter: api.patchCharacter,
}));
vi.mock("../../api/modelShelf", () => ({ listAdapters: api.listAdapters }));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));
vi.mock("../../api/workflows", () => ({
  getRunOutcome: api.getRunOutcome,
  getWorkflowCard: api.getWorkflowCard,
  listWorkflowCards: api.listWorkflowCards,
  preflightWorkflowRun: api.preflightWorkflowRun,
  runWorkflowCard: api.runWorkflowCard,
  sheetCopyWorkflow: api.sheetCopyWorkflow,
}));

import CharacterSheetStep from "./CharacterSheetStep.vue";

const MOSS = {
  id: "auto:moss",
  name: "MOSS sheet",
  type: "txt2img",
  models: [{ kind: "checkpoint", base_model_family: "moss_image" }],
};
const QWEN = {
  id: "auto:qwen",
  name: "Qwen-Image 2.1 Sheet",
  type: "txt2img",
  last_used: "2026-11-24",
  models: [{ kind: "unet", base_model_family: "qwen" }],
};
const MINIMAX = {
  id: "auto:h3",
  name: "MiniMax H3 Image Sheet",
  type: "txt2img",
  built_in: true,
  models: [{ kind: "unet", base_model_family: "minimax_h3_image" }],
};
// The owner's own MiniMax graph, used last: it may save several pictures a
// run, so the shipped one still comes first.
const MINIMAX_OWN = {
  id: "manual:own",
  name: "SABsheet-test",
  type: "txt2img",
  last_used: "2026-11-25",
  models: [{ kind: "unet", base_model_family: "minimax_h3_image" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  setActivePinia(createPinia());
  localStorage.clear();
  api.listWorkflowCards.mockResolvedValue({
    cards: [MOSS, QWEN, MINIMAX_OWN, MINIMAX],
  });
  api.getWorkflowCard.mockResolvedValue({
    card: {
      name: "sheet",
      defaults: [
        { slot_label: "lat", input_name: "width", value: 1024 },
        { slot_label: "lat", input_name: "height", value: 1024 },
      ],
    },
  });
  api.preflightWorkflowRun.mockResolvedValue({ ok: true, groups: [{ reasons: [] }] });
  api.buildSheetPrompt.mockResolvedValue({
    prompt: "Outfit reference sheet…",
    negative: "nsfw",
    width: 3840,
    height: 2304,
  });
  api.runWorkflowCard.mockResolvedValue({
    prompts: [{ run_id: "r1", status: "queued" }],
  });
  api.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [41] });
  api.rankItemSheets.mockResolvedValue({
    sheets: [
      {
        picture_id: 41,
        format: "png",
        readable: true,
        agreement: 0.9,
        agreement_words: "the four angles agree",
        cuts_cleanly: true,
        cells: [],
        panels: [0, 1, 2, 3].map((panel) => ({
          panel,
          box: [panel / 4, 0, (panel + 1) / 4, 1],
          agreement: 0.9,
          agrees: true,
        })),
      },
    ],
  });
  api.useItemSheets.mockResolvedValue({ picture_ids: [51, 52, 53, 54] });
  api.createPictureSet.mockResolvedValue({
    picture_set: { id: 8, name: "Mustard raincoat" },
  });
  api.bulkAddPicturesToSet.mockResolvedValue({});
});

afterEach(() => {
  vi.useRealTimers();
});

function mountOutfit() {
  return mount(CharacterSheetStep, {
    props: { kind: "outfit", character: { id: 3, name: "Mira" } },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  });
}

/** Describe, draw one sheet and wait for it to be ranked and kept. */
async function drawOne(wrapper) {
  await wrapper.find('[data-testid="cs-description"] textarea').setValue(
    "a mustard raincoat",
  );
  await flushPromises();
  await wrapper.vm.footer.primary.run();
  await flushPromises();
  await vi.advanceTimersByTimeAsync(3000);
  await flushPromises();
}

describe("CharacterSheetStep: an outfit", () => {
  it("draws in prose, ranks without faces and files the panels in a set", async () => {
    const wrapper = mountOutfit();
    await flushPromises();
    // A character's own reads are not made, and MOSS draws no item sheet.
    expect(api.getCharacter).not.toHaveBeenCalled();
    expect(api.listAdapters).not.toHaveBeenCalled();
    const offered = wrapper.vm.workflowOptions.map((o) => o.value);
    expect(offered).toEqual(["auto:h3", "manual:own", "auto:qwen"]);
    expect(wrapper.vm.workflowId).toBe("auto:h3");
    // Description, name and count: none of a character's controls, no size,
    // look or sampler, and no drawing from a picture.
    for (const id of [
      "cs-lora",
      "cs-framing",
      "cs-size",
      "cs-photo-look",
      "cs-sampler",
      "cs-mode",
    ]) {
      expect(wrapper.find(`[data-testid="${id}"]`).exists(), id).toBe(false);
    }
    expect(wrapper.vm.form.count).toBe(2);

    await wrapper.find('[data-testid="cs-description"] textarea').setValue(
      "a mustard raincoat",
    );
    wrapper.findComponent('[data-testid="cs-item-name"]').vm.$emit(
      "update:modelValue",
      "Mustard raincoat",
    );
    await flushPromises();
    await wrapper.vm.footer.primary.run();
    await flushPromises();
    expect(api.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "outfit",
        description: "a mustard raincoat",
        layout: "prose",
      }),
    );
    expect(api.patchCharacter).not.toHaveBeenCalled();
    expect(api.runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({
        workflow_id: "auto:h3",
        hold: true,
        count: 2,
        label: "Outfit sheet · Mustard raincoat",
      }),
    );
    const run = api.runWorkflowCard.mock.calls[0][0];
    expect(run.prompt_assist).toBeUndefined();
    expect(run.sampler).toBeUndefined();

    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(api.rankItemSheets).toHaveBeenCalledWith("outfit", [41]);
    expect(api.rankSheets).not.toHaveBeenCalled();
    expect(wrapper.vm.phase).toBe("pick");
    // The best sheet is kept for the owner, as a character's is.
    expect(wrapper.vm.footer.primary.label).toBe("Use sheet 1");
    expect(wrapper.vm.footer.primary.enabled).toBe(true);

    await wrapper.vm.footer.primary.run();
    await flushPromises();
    expect(api.useItemSheets).toHaveBeenCalledWith("outfit", [41], {});
    expect(api.createPictureSet).toHaveBeenCalledWith({
      name: "Mustard raincoat",
      description: "a mustard raincoat",
      parent_character_id: 3,
    });
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(8, [51, 52, 53, 54]);
    expect(wrapper.emitted("done")[0]).toEqual([
      [51, 52, 53, 54],
      { id: 8, name: "Mustard raincoat" },
    ]);
    wrapper.unmount();
  });

  it("can draw once the chosen workflow passes, before the rest answer", async () => {
    // A large library pre-flights a hundred workflows; one still out must not
    // hold Draw back.
    const ok = { ok: true, groups: [{ reasons: [] }] };
    api.preflightWorkflowRun.mockImplementation(({ workflow_id }) =>
      workflow_id === "auto:qwen" ? new Promise(() => {}) : Promise.resolve(ok),
    );
    const wrapper = mountOutfit();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("a jumper");
    await flushPromises();
    expect(wrapper.vm.workflowId).toBe("auto:h3");
    expect(wrapper.vm.footer.primary.enabled).toBe(true);
    wrapper.unmount();
  });

  it("stops before cutting when the set's name is taken", async () => {
    api.createPictureSet.mockRejectedValueOnce({
      response: { status: 409, data: { detail: "A set named New outfit exists" } },
    });
    const wrapper = mountOutfit();
    await flushPromises();
    await drawOne(wrapper);
    expect(wrapper.vm.phase).toBe("pick");
    await wrapper.vm.footer.primary.run();
    await flushPromises();
    expect(api.createPictureSet).toHaveBeenCalledWith(
      expect.objectContaining({ name: "New outfit" }),
    );
    // Nothing is cut, the sheets stay on show and say why.
    expect(api.useItemSheets).not.toHaveBeenCalled();
    expect(wrapper.vm.phase).toBe("pick");
    expect(wrapper.vm.error).toContain("Couldn't make the set New outfit");
    expect(wrapper.emitted("cut")).toBeUndefined();
    wrapper.unmount();
  });

  it("takes the empty set away when the cut fails", async () => {
    api.useItemSheets.mockRejectedValueOnce(new Error("unreadable"));
    const wrapper = mountOutfit();
    await flushPromises();
    await drawOne(wrapper);
    await wrapper.vm.footer.primary.run();
    await flushPromises();
    expect(api.deletePictureSet).toHaveBeenCalledWith(8);
    expect(api.bulkAddPicturesToSet).not.toHaveBeenCalled();
    expect(wrapper.vm.phase).toBe("pick");
    wrapper.unmount();
  });
});
