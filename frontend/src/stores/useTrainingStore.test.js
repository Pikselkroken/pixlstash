// The Train a LoRA dialog's state (#2119): what it trains on, the pins it
// holds, and that Start sends exactly what the preview resolved.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/training", () => ({
  cancelTrainingJob: vi.fn(),
  checkAiToolkit: vi.fn(),
  createTrainingJob: vi.fn(),
  deleteTrainingJob: vi.fn(),
  discoverAiToolkit: vi.fn(),
  getAiToolkitStatus: vi.fn(),
  getTrainingDataset: vi.fn(),
  getTrainingSamplePrompts: vi.fn(),
  listTrainingJobs: vi.fn(),
  listTrainingPresets: vi.fn(),
  putAiToolkitConfig: vi.fn(),
  resolveTraining: vi.fn(),
}));
vi.mock("../api/config", () => ({ getUserConfig: vi.fn() }));

import * as training from "../api/training";
import { getUserConfig } from "../api/config";
import { useTrainingStore } from "./useTrainingStore";

const T = "{{TRIGGER_WORD}}";
const GRANDAD = {
  id: 4,
  name: "Grandad",
  reference_set_id: 40,
  reference_count: 24,
  all_count: 140,
};

function dataset(over = {}) {
  return {
    set_id: null,
    character_id: null,
    count: 24,
    min_pictures: 6,
    untagged: 0,
    near_duplicates: 0,
    other_people: 0,
    character: null,
    ...over,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  training.createTrainingJob.mockResolvedValue({ id: 1 });
  training.listTrainingJobs.mockResolvedValue([]);
  training.listTrainingPresets.mockResolvedValue([
    { id: "zimage-turbo", label: "Z-Image Turbo", offered: true },
  ]);
  training.resolveTraining.mockResolvedValue({ derived: {} });
  training.getTrainingSamplePrompts.mockResolvedValue({
    prompts: [
      { prompt: `${T} by the sea`, with_trigger: `${T} by the sea`, without_trigger: "a man by the sea" },
      { prompt: "a man by the sea", with_trigger: `${T} by the sea`, without_trigger: "a man by the sea" },
    ],
  });
});

describe("openForScope", () => {
  it("trains a person on their reference set, filed under them", async () => {
    training.getTrainingDataset.mockImplementation(async (params) =>
      params.set_id === 40
        ? dataset({ set_id: 40, character_id: 4, character: GRANDAD })
        : dataset({ character_id: 4, count: 140, character: GRANDAD }),
    );
    const store = useTrainingStore();
    await store.openForScope({ type: "character", id: 4, name: "Grandad" });

    expect(training.getTrainingDataset).toHaveBeenNthCalledWith(1, { character_id: 4 });
    expect(training.getTrainingDataset).toHaveBeenLastCalledWith({
      set_id: 40,
      character_id: 4,
    });
    expect(store.trainOn).toBe("reference");
    expect(store.triggerWord).toBe("grandad");
    expect(store.effectiveSubject).toBe("person");
    expect(store.pictureCount).toBe(24);

    await store.setTrainOn("all");
    expect(training.getTrainingDataset).toHaveBeenLastCalledWith({ character_id: 4 });
    expect(store.pictureCount).toBe(140);
  });

  it("asks a set what it is of, and takes the trigger word from its name", async () => {
    training.getTrainingDataset.mockResolvedValue(dataset({ set_id: 9, count: 112 }));
    const store = useTrainingStore();
    await store.openForScope({ type: "set", id: 9, name: "Pub nights" });
    expect(store.character).toBe(null);
    expect(store.effectiveSubject).toBe(null);
    expect(store.triggerWord).toBe("pubnights");
  });

  it("trains a set under a person as a character LoRA of them, on that set (#2124)", async () => {
    const child = { id: 12, name: "LoRA v3", count: 32 };
    training.getTrainingDataset.mockImplementation(async (params) =>
      params.set_id === 12
        ? dataset({
            set_id: 12,
            character_id: 4,
            count: 32,
            character: { ...GRANDAD, child_set: child },
          })
        : dataset({ character_id: 4, count: 140, character: GRANDAD }),
    );
    const store = useTrainingStore();
    await store.openForScope({ type: "set", id: 12, name: "LoRA v3" });

    expect(training.getTrainingDataset).toHaveBeenCalledTimes(1);
    expect(store.trainOn).toBe("set");
    expect(store.scopePayload).toEqual({ set_id: 12, character_id: 4 });
    expect(store.effectiveSubject).toBe("person");
    expect(store.triggerWord).toBe("grandad");
    expect(store.pictureCount).toBe(32);

    // Every picture of the person, and back: the set is still offered.
    await store.setTrainOn("all");
    expect(store.pictureCount).toBe(140);
    expect(store.childSet).toEqual(child);
    await store.setTrainOn("set");
    expect(training.getTrainingDataset).toHaveBeenLastCalledWith({
      set_id: 12,
      character_id: 4,
    });
  });

  it("starts a set under a set on A thing, a concept LoRA (#2124)", async () => {
    training.getTrainingDataset.mockResolvedValue(
      dataset({ set_id: 13, count: 30, parent_set_id: 9 }),
    );
    const store = useTrainingStore();
    await store.openForScope({ type: "set", id: 13, name: "Neon concept v1" });
    expect(store.character).toBe(null);
    expect(store.subject).toBe("thing");
  });

  it("asks the server nothing once disposed with a resolve pending (#2220)", async () => {
    vi.useFakeTimers();
    try {
      training.getTrainingDataset.mockResolvedValue(
        dataset({ set_id: 40, character_id: 4, character: GRANDAD }),
      );
      const store = useTrainingStore();
      await store.openForScope({ type: "character", id: 4, name: "Grandad" });
      store.triggerWord = "grandpa";
      await Promise.resolve();
      expect(vi.getTimerCount()).toBe(2);
      const promptCalls = training.getTrainingSamplePrompts.mock.calls.length;
      store.$dispose();
      await vi.advanceTimersByTimeAsync(1000);
      expect(training.resolveTraining).not.toHaveBeenCalled();
      expect(training.getTrainingSamplePrompts).toHaveBeenCalledTimes(promptCalls);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("pins", () => {
  it("drops a typed value equal to the derived one, keeps any other", () => {
    const store = useTrainingStore();
    store.plan = {
      derived: { "train.steps": { value: 2400, source: "pictures" } },
      choicesKey: store.choicesKey,
    };
    store.setPin("train.steps", 2000);
    expect(store.pins).toEqual({ "train.steps": 2000 });
    store.setPin("train.steps", 2400);
    expect(store.pins).toEqual({});
    store.setPin("train.steps", 1800);
    store.setPin("train.steps", null);
    expect(store.pins).toEqual({});
  });

  it("keeps a typed value while the plan still answers the previous choices", async () => {
    training.getTrainingDataset.mockResolvedValue(
      dataset({ set_id: 40, character_id: 4, character: GRANDAD }),
    );
    training.resolveTraining.mockImplementation(async (body) => ({
      derived: {
        "train.steps": {
          value: body.effort === "strong" ? 3600 : 2400,
          source: "pictures",
        },
      },
    }));
    const store = useTrainingStore();
    await store.openForScope({ type: "character", id: 4, name: "Grandad" });
    await store.resolvePlan();
    // Strong is picked, then 2400 typed before its plan arrives: 2400 is what
    // Standard gave, not Strong, so it is a pin.
    store.effort = "strong";
    store.setPin("train.steps", 2400);
    expect(store.pins).toEqual({ "train.steps": 2400 });
    // Once the plan answers Strong, typing what Strong gives is no pin.
    await store.resolvePlan();
    store.setPin("train.steps", 3600);
    expect(store.pins).toEqual({});
  });

  it("sends an edited prompt set as a pin, and the written one as none", async () => {
    training.getTrainingDataset.mockResolvedValue(
      dataset({ set_id: 40, character_id: 4, character: GRANDAD }),
    );
    const store = useTrainingStore();
    await store.openForScope({ type: "set", id: 40, name: "Grandad reference" });
    await store.loadPrompts();
    expect(store.prompts).toHaveLength(2);
    expect(store.promptsPinned).toBe(false);
    expect(store.allPins).toEqual({});

    store.removePrompt(1);
    store.addPrompt();
    store.setPromptText(1, "grandad reading, warm light");
    expect(store.promptsPinned).toBe(true);
    expect(store.allPins).toEqual({
      "sample.samples[*].prompt": [`${T} by the sea`, `${T} reading, warm light`],
    });

    // A fresh set of written prompts does not replace edited lines.
    await store.loadPrompts();
    expect(store.prompts[1].text).toBe(`${T} reading, warm light`);

    store.unpin(["sample.samples[*].prompt"]);
    expect(store.promptsPinned).toBe(false);
  });
});

describe("startTraining", () => {
  it("sends the scope, the choices and every pin", async () => {
    training.getTrainingDataset.mockResolvedValue(
      dataset({ set_id: 40, character_id: 4, character: GRANDAD }),
    );
    const store = useTrainingStore();
    await store.openForScope({ type: "character", id: 4, name: "Grandad" });
    store.fit = "save_memory";
    store.plan = {
      derived: { "train.steps": { value: 2400, source: "pictures" } },
      choicesKey: store.choicesKey,
    };
    store.setPin("train.steps", 2000);
    training.listTrainingJobs.mockResolvedValue([
      { name: "Grandad Z-Image Turbo #1" },
      { name: "Grandad (grandad)" },
    ]);
    await store.startTraining();

    expect(training.createTrainingJob).toHaveBeenCalledWith({
      set_id: 40,
      character_id: 4,
      name: "Grandad Z-Image Turbo #2",
      trigger_word: "grandad",
      preset: "zimage-turbo",
      subject: "person",
      effort: "standard",
      fit: "save_memory",
      pins: { "train.steps": 2000 },
    });
  });

  it("sends no pins when nothing was typed over", async () => {
    training.getTrainingDataset.mockResolvedValue(dataset({ set_id: 9 }));
    const store = useTrainingStore();
    await store.openForScope({ type: "set", id: 9, name: "Pub nights" });
    store.subject = "style";
    await store.startTraining();
    const body = training.createTrainingJob.mock.calls[0][0];
    expect(body.set_id).toBe(9);
    expect(body.subject).toBe("style");
    expect(body).not.toHaveProperty("pins");
  });

  it("shows the message of a run that fits nowhere", async () => {
    const store = useTrainingStore();
    training.createTrainingJob.mockRejectedValue({
      response: {
        data: {
          detail: {
            message: "Z-Image Turbo needs 10.5 GB of GPU memory at least",
            budget: "vram",
          },
        },
      },
    });
    await expect(store.startTraining()).rejects.toBeTruthy();
    expect(store.submitError).toBe(
      "Z-Image Turbo needs 10.5 GB of GPU memory at least",
    );
  });
});

// FastAPI answers a 422 with `detail` as a list of objects; the page must
// show a sentence, never "[object Object]" (#2295).
const VALIDATION_422 = {
  response: {
    data: { detail: [{ loc: ["query", "set_id"], msg: "field required", type: "missing" }] },
  },
};

describe("error copy from a refused request", () => {
  it("shows a refused dataset read as its fallback sentence", async () => {
    const store = useTrainingStore();
    training.getTrainingDataset.mockRejectedValue(VALIDATION_422);
    await store.openForScope({ type: "set", id: 9, name: "Pub nights" });
    await vi.waitFor(() => expect(store.datasetError).not.toBe(""));
    expect(store.datasetError).toBe("Could not read the pictures to train on.");
  });

  it("shows a refused job list as its fallback sentence", async () => {
    const store = useTrainingStore();
    training.listTrainingJobs.mockRejectedValue(VALIDATION_422);
    await store.fetchJobs();
    expect(store.jobsError).toBe("Failed to load training jobs.");
  });
});

describe("fetchConfig", () => {
  it("counts the install as set up from the flag a remote owner gets without paths", async () => {
    const store = useTrainingStore();
    getUserConfig.mockResolvedValue({
      ai_toolkit_repo_path: null,
      ai_toolkit_python: null,
      ai_toolkit_configured: true,
    });
    await store.fetchConfig();
    expect(store.aiToolkitConfigured).toBe(true);
    expect(store.aiToolkitRepoPath).toBe("");
  });
});

describe("saveAiToolkitPaths", () => {
  it("keeps what the server stored, not what was typed", async () => {
    const store = useTrainingStore();
    training.putAiToolkitConfig.mockResolvedValue({ repo_path: "/home/me/ai-toolkit", python: null });
    await store.saveAiToolkitPaths("/home/me/ai-toolkit", "  ");
    expect(store.aiToolkitPython).toBe("");
    expect(store.aiToolkitConfigured).toBe(false);
  });
});
