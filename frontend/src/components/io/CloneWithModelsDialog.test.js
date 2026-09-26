// Clone with new models: the cascade, and the three ways it can lie.
//
// * A companion row filled by a proposal must say which step answered it.
// * A row nothing answers keeps the workflow's own file and says so, rather
//   than reading as a recommendation.
// * Clone stays off until something would change: a clone that swaps nothing
//   lands on the original card and looks like a gesture that did nothing.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const readModelSwap = vi.fn();
const cloneWorkflowWithModels = vi.fn();
vi.mock("../../api/workflows", () => ({
  readModelSwap: (...args) => readModelSwap(...args),
  cloneWorkflowWithModels: (...args) => cloneWorkflowWithModels(...args),
  listWorkflowCards: vi.fn(async () => ({ cards: [], one_offs: 0, hidden: 0 })),
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import CloneWithModelsDialog from "./CloneWithModelsDialog.vue";

const KEY = "a".repeat(64);
const ZIMAGE = {
  id: 1,
  filename: "zimage-turbo.safetensors",
  display_name: "Z-Image Turbo",
  base_model: "Z-Image Turbo",
  file_kind: "checkpoint",
};
const KREA = {
  id: 2,
  filename: "krea2.safetensors",
  display_name: "Krea 2",
  base_model: "FLUX.2",
  file_kind: "checkpoint",
};

const OPTIONS = {
  slots: [
    { filename: "zimage-turbo.safetensors", kind: "unet", model: ZIMAGE },
    { filename: "ae.safetensors", kind: "vae", model: null },
    { filename: "qwen_3_4b.safetensors", kind: "clip", model: null },
    { filename: "t5xxl.safetensors", kind: "clip", model: null },
    { filename: "style.safetensors", kind: "lora", model: null },
  ],
  checkpoints: [KREA, ZIMAGE],
  vaes: [
    {
      id: 3,
      filename: "flux2-vae.safetensors",
      display_name: null,
      file_kind: "vae",
    },
  ],
  text_encoders: [
    {
      id: 4,
      filename: "mistral.safetensors",
      display_name: null,
      file_kind: "text_encoder",
    },
  ],
  proposals: {},
  flags: [],
};

const CHOSEN = {
  ...OPTIONS,
  checkpoint_family: "flux2",
  checkpoint_modality: "image",
  proposals: {
    vae: [
      {
        id: 3,
        filename: "flux2-vae.safetensors",
        via: "checkpoint",
        recipes: 4,
      },
    ],
    text_encoder: [
      { id: 4, filename: "mistral.safetensors", via: "base_model", recipes: 1 },
    ],
  },
  flags: [
    {
      filename: "style.safetensors",
      kind: "lora",
      base_model: "Z-Image Turbo",
      family: "zimage",
      modality: "image",
    },
  ],
};

function open() {
  return mount(CloneWithModelsDialog, {
    props: { open: true, workflowKey: KEY, cardName: "Portrait" },
    global: { stubs: { teleport: true } },
  });
}

function selects(wrapper) {
  return wrapper.findAll("select");
}

function cloneButton(wrapper) {
  return wrapper.findAll("button").find((b) => b.text().includes("Clone"));
}

describe("CloneWithModelsDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null ? OPTIONS : CHOSEN,
    );
    cloneWorkflowWithModels.mockResolvedValue({
      name: "Portrait — Krea 2.json",
      workflow_key: "b".repeat(64),
      swapped: [],
      unswapped: [],
      verified: true,
    });
  });

  it("opens on the workflow's own models with Clone off", async () => {
    const wrapper = open();
    await flushPromises();
    const [checkpoint, vae, clipA, clipB] = selects(wrapper);
    expect(checkpoint.element.value).toBe("1");
    expect(vae.element.value).toBe("ae.safetensors");
    expect(clipA.element.value).toBe("qwen_3_4b.safetensors");
    expect(clipB.element.value).toBe("t5xxl.safetensors");
    expect(wrapper.text()).toContain("1 LoRA stays as it is.");
    // The workflow ran with its own companions: nothing to warn about.
    expect(wrapper.text()).not.toContain("Nothing on your shelf");
    expect(cloneButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("fills the companions with their provenance and marks the unanswered row", async () => {
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    expect(readModelSwap).toHaveBeenLastCalledWith(KEY, { checkpointId: 2 });
    const [, vae, clipA, clipB] = selects(wrapper);
    expect(vae.element.value).toBe("flux2-vae.safetensors");
    expect(clipA.element.value).toBe("mistral.safetensors");
    // Only one text encoder was proposed: the second keeps the workflow's.
    expect(clipB.element.value).toBe("t5xxl.safetensors");
    const text = wrapper.text();
    expect(text).toContain("Used with this checkpoint");
    expect(text).toContain("Used with other FLUX.2 checkpoints");
    // The one proposal went to the first row: that is not "nothing ran".
    expect(text).toContain("The suggestion went to another row");
    expect(text).not.toContain("Nothing on your shelf");
    // Flagged, never dropped; both image models, so no modality words.
    expect(text).toContain(
      "“style.safetensors” was trained on Z-Image Turbo; this checkpoint is FLUX.2.",
    );
  });

  it("says when a flagged LoRA is for a video model and the checkpoint an image one", async () => {
    readModelSwap.mockImplementation((key, { checkpointId } = {}) =>
      Promise.resolve(
        checkpointId == null
          ? OPTIONS
          : {
              ...CHOSEN,
              flags: [
                {
                  filename: "motion.safetensors",
                  kind: "lora",
                  base_model: "Wan 2.2",
                  family: "wan",
                  modality: "video",
                },
              ],
            },
      ),
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    expect(wrapper.text()).toContain(
      "“motion.safetensors” was trained on Wan 2.2, a video model; this checkpoint is FLUX.2, an image model.",
    );
  });

  it("says a declared proposal is untested, in the warning tone", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? OPTIONS
        : {
            ...OPTIONS,
            proposals: {
              vae: [
                {
                  id: 3,
                  filename: "flux2-vae.safetensors",
                  family: "vae_16ch",
                  via: "declared",
                  recipes: 0,
                },
              ],
            },
          },
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("flux2-vae.safetensors");
    const note = wrapper
      .findAll(".cwm-note")
      .find((n) => n.text().startsWith("Untested"));
    expect(note).toBeDefined();
    expect(note.classes()).toContain("cwm-warn");
    expect(wrapper.text()).not.toContain("Used with");
  });

  it("pre-picks a grouped file, labels it, and lists it ahead of the shelf (#1520)", async () => {
    const GROUPED_VAE = {
      id: 5,
      filename: "Grouped-VAE.safetensors",
      display_name: null,
      file_kind: "vae",
    };
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? { ...OPTIONS, vaes: [...OPTIONS.vaes, GROUPED_VAE] }
        : {
            ...CHOSEN,
            vaes: [...OPTIONS.vaes, GROUPED_VAE],
            proposals: {
              ...CHOSEN.proposals,
              vae: [
                {
                  id: 5,
                  filename: "Grouped-VAE.safetensors",
                  via: "grouped",
                  recipes: 0,
                  set_name: "Night",
                  prepick: true,
                },
                ...CHOSEN.proposals.vae,
              ],
            },
          },
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();

    const vae = selects(wrapper)[1];
    expect(vae.element.value).toBe("Grouped-VAE.safetensors");
    expect(wrapper.text()).toContain('Grouped by you in "Night"');
    // After the workflow's own file, which always leads, the grouped file
    // heads the shelf's options, labelled.
    const labels = vae.findAll("option").map((option) => option.text());
    expect(labels.slice(1, 3)).toEqual([
      "Grouped-VAE.safetensors · Grouped by you",
      expect.not.stringContaining("Grouped by you"),
    ]);
  });

  it("does not pre-pick a grouped file the set offers alongside others", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? OPTIONS
        : {
            ...CHOSEN,
            proposals: {
              ...CHOSEN.proposals,
              vae: [
                {
                  id: 3,
                  filename: "flux2-vae.safetensors",
                  via: "grouped",
                  recipes: 0,
                  set_name: null,
                  prepick: false,
                },
              ],
            },
          },
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();

    expect(selects(wrapper)[1].element.value).toBe("ae.safetensors");
    expect(wrapper.text()).not.toContain("Grouped by you in");
  });

  it("clones by filename, only what changed, under the typed name", async () => {
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    expect(cloneButton(wrapper).attributes("disabled")).toBeUndefined();
    await cloneButton(wrapper).trigger("click");
    await flushPromises();
    expect(cloneWorkflowWithModels).toHaveBeenCalledWith(KEY, {
      name: "Portrait — Krea 2",
      swaps: {
        "zimage-turbo.safetensors": "krea2.safetensors",
        "ae.safetensors": "flux2-vae.safetensors",
        "qwen_3_4b.safetensors": "mistral.safetensors",
      },
    });
    expect(wrapper.emitted("cloned")?.[0]?.[0].workflow_key).toBe(
      "b".repeat(64),
    );
  });

  it("ignores a proposal that arrives after the choice was undone", async () => {
    let answer;
    readModelSwap.mockImplementation((key, { checkpointId } = {}) =>
      checkpointId == null
        ? Promise.resolve(OPTIONS)
        : new Promise((resolve) => {
            answer = () => resolve(CHOSEN);
          }),
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    // Mid-read, Clone would post the checkpoint without its companions.
    expect(cloneButton(wrapper).attributes("disabled")).toBeDefined();
    await selects(wrapper)[0].setValue("1");
    await flushPromises();
    answer();
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("ae.safetensors");
    expect(wrapper.text()).not.toContain("Used with this checkpoint");
    expect(wrapper.text()).not.toContain("was trained on");
  });

  it("puts a proposal only into a slot of the same encoder layout", async () => {
    const t5 = {
      id: 9,
      filename: "t5xxl.safetensors",
      display_name: null,
      file_kind: "text_encoder",
      family: "t5_xxl",
    };
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? {
            ...OPTIONS,
            slots: [
              OPTIONS.slots[0],
              { filename: "t5xxl.safetensors", kind: "clip", model: t5 },
            ],
            text_encoders: [
              t5,
              {
                id: 4,
                filename: "clip_l.safetensors",
                display_name: null,
                file_kind: "text_encoder",
                family: "clip_l",
              },
              {
                id: 5,
                filename: "t5xxl_fp16.safetensors",
                display_name: null,
                file_kind: "text_encoder",
                family: "t5_xxl",
              },
            ],
          }
        : {
            ...CHOSEN,
            proposals: {
              vae: [],
              text_encoder: [
                {
                  id: 4,
                  filename: "clip_l.safetensors",
                  family: "clip_l",
                  via: "checkpoint",
                  recipes: 3,
                },
                {
                  id: 5,
                  filename: "t5xxl_fp16.safetensors",
                  family: "t5_xxl",
                  via: "checkpoint",
                  recipes: 3,
                },
              ],
            },
          },
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("t5xxl_fp16.safetensors");
  });

  it("reads the new card's models when the card changes while open", async () => {
    const wrapper = open();
    await flushPromises();
    await wrapper.setProps({ workflowKey: "c".repeat(64) });
    await flushPromises();
    expect(wrapper.text()).not.toContain("Reading this workflow's models");
    expect(selects(wrapper)[0].element.value).toBe("1");
  });

  it("says a row kept its file for the reason it did, whichever row took the proposal", async () => {
    const t5 = {
      id: 9,
      filename: "t5xxl.safetensors",
      display_name: null,
      file_kind: "text_encoder",
      family: "t5_xxl",
    };
    const clipL = {
      id: 8,
      filename: "clip_l_old.safetensors",
      display_name: null,
      file_kind: "text_encoder",
      family: "clip_l",
    };
    const newClipL = {
      id: 4,
      filename: "clip_l.safetensors",
      display_name: null,
      file_kind: "text_encoder",
      family: "clip_l",
    };
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? {
            ...OPTIONS,
            // The T5 row is ABOVE the CLIP-L row that takes the only proposal.
            slots: [
              OPTIONS.slots[0],
              { filename: "t5xxl.safetensors", kind: "clip", model: t5 },
              {
                filename: "clip_l_old.safetensors",
                kind: "clip",
                model: clipL,
              },
            ],
            text_encoders: [t5, clipL, newClipL],
          }
        : {
            ...CHOSEN,
            proposals: {
              vae: [],
              text_encoder: [{ ...newClipL, via: "checkpoint", recipes: 2 }],
            },
          },
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("t5xxl.safetensors");
    expect(selects(wrapper)[2].element.value).toBe("clip_l.safetensors");
    const text = wrapper.text();
    expect(text).toContain("Nothing of this encoder type has run");
    expect(text).not.toContain("went to another row");
  });

  it("counts another shelf file of the same basename as a swap", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? {
            ...OPTIONS,
            vaes: [
              {
                id: 3,
                filename: "ae.safetensors",
                display_name: null,
                file_kind: "vae",
              },
              {
                id: 6,
                filename: "sd3/ae.safetensors",
                display_name: null,
                file_kind: "vae",
              },
            ],
            slots: [
              OPTIONS.slots[0],
              {
                filename: "ae.safetensors",
                kind: "vae",
                model: { id: 3, filename: "ae.safetensors", file_kind: "vae" },
              },
            ],
          }
        : CHOSEN,
    );
    const wrapper = open();
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("ae.safetensors");
    await selects(wrapper)[1].setValue("sd3/ae.safetensors");
    await flushPromises();
    await cloneButton(wrapper).trigger("click");
    await flushPromises();
    expect(cloneWorkflowWithModels.mock.calls[0][1].swaps).toEqual({
      "ae.safetensors": "sd3/ae.safetensors",
    });
  });

  it("never takes another shelf row's proposal as evidence for this row's own file", async () => {
    const own = {
      id: 3,
      filename: "ae.safetensors",
      display_name: null,
      file_kind: "vae",
    };
    const other = {
      id: 6,
      filename: "sd3/ae.safetensors",
      display_name: null,
      file_kind: "vae",
    };
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? {
            ...OPTIONS,
            vaes: [own, other],
            slots: [
              OPTIONS.slots[0],
              { filename: "ae.safetensors", kind: "vae", model: own },
            ],
          }
        : {
            ...CHOSEN,
            proposals: {
              vae: [{ ...other, via: "checkpoint", recipes: 1 }],
              text_encoder: [],
            },
          },
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    // The proposal is the OTHER file: it is offered as a swap, not claimed
    // for the file the row already holds.
    expect(selects(wrapper)[1].element.value).toBe("sd3/ae.safetensors");
  });

  it("says so when ComfyUI's run history is the only evidence", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? OPTIONS
        : {
            ...CHOSEN,
            proposals: {
              vae: [{ ...CHOSEN.proposals.vae[0], recipes: 0, history_runs: 2 }],
              text_encoder: CHOSEN.proposals.text_encoder,
            },
          },
    );
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    const text = wrapper.text();
    expect(text).toContain("Used with this checkpoint in ComfyUI");
    // A recipe-backed proposal keeps its plain wording.
    expect(text).toContain("Used with other FLUX.2 checkpoints");
    expect(text).not.toContain("FLUX.2 checkpoints in ComfyUI");
  });

  it("keeps the workflow's own checkpoint in the select when the server left it out", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null ? { ...OPTIONS, checkpoints: [KREA] } : CHOSEN,
    );
    const wrapper = open();
    await flushPromises();
    const select = selects(wrapper)[0];
    expect(select.element.value).toBe("1");
    expect(select.text()).toContain("Z-Image Turbo (the workflow's)");
  });

  it("clones nothing into a checkpoint whose proposal read failed", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) => {
      if (checkpointId == null) return OPTIONS;
      throw new Error("boom");
    });
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("ae.safetensors");
    expect(wrapper.text()).not.toContain("Used with");
    expect(cloneButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("never reads the unchanged checkpoint as a swap when the shelf names it differently", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? {
            ...OPTIONS,
            slots: [
              {
                filename: "RealVisXL_V4.0.safetensors",
                kind: "checkpoint",
                model: ZIMAGE,
              },
              ...OPTIONS.slots.slice(1),
            ],
          }
        : CHOSEN,
    );
    const wrapper = open();
    await flushPromises();
    expect(cloneButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("shows the shelf's spelling of the workflow's own file, never a blank select", async () => {
    readModelSwap.mockImplementation(async (key, { checkpointId } = {}) =>
      checkpointId == null
        ? {
            ...OPTIONS,
            slots: [
              OPTIONS.slots[0],
              {
                filename: "vae/flux2-vae.safetensors",
                kind: "vae",
                model: null,
              },
            ],
          }
        : CHOSEN,
    );
    const wrapper = open();
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("flux2-vae.safetensors");
    expect(cloneButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("goes back to nothing to clone when the original checkpoint is chosen again", async () => {
    const wrapper = open();
    await flushPromises();
    await selects(wrapper)[0].setValue("2");
    await flushPromises();
    await selects(wrapper)[0].setValue("1");
    await flushPromises();
    expect(selects(wrapper)[1].element.value).toBe("ae.safetensors");
    expect(cloneButton(wrapper).attributes("disabled")).toBeDefined();
  });
});
