// The train panel's request: the recipe, the three choices and the Advanced
// fields as flat pins (#2116). The raw config patch is gone.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/training", () => ({
  cancelTrainingJob: vi.fn(),
  checkAiToolkit: vi.fn(),
  createTrainingJob: vi.fn(),
  deleteTrainingJob: vi.fn(),
  discoverAiToolkit: vi.fn(),
  getAiToolkitStatus: vi.fn(),
  listTrainingJobs: vi.fn(),
  listTrainingPresets: vi.fn(),
  putAiToolkitConfig: vi.fn(),
}));

import * as training from "../api/training";
import { useTrainingStore } from "./useTrainingStore";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  training.createTrainingJob.mockResolvedValue({ id: 1 });
});

describe("startTraining", () => {
  it("sends the choices and no pins when Advanced is untouched", async () => {
    const store = useTrainingStore();
    store.setScope({ type: "character", id: 4, name: "Grandad" });
    store.selectedPreset = "zimage-turbo";
    await store.startTraining();

    expect(training.createTrainingJob).toHaveBeenCalledWith({
      character_id: 4,
      name: "Grandad (Grandad)",
      trigger_word: "Grandad",
      preset: "zimage-turbo",
      subject: "person",
      effort: "standard",
      fit: "auto",
    });
  });

  it("sends only the filled Advanced fields, keyed by setting", async () => {
    const store = useTrainingStore();
    store.setScope({ type: "set", id: 9, name: "Pub nights" });
    expect(store.subject).toBe(null);
    store.subject = "style";
    store.fit = "save_memory";
    store.steps = 1800;
    store.networkRank = 16;
    store.learningRate = "0.0002";
    store.samplePrompts = "{{TRIGGER_WORD}}, a harbour\n\n a harbour ";
    await store.startTraining();

    const body = training.createTrainingJob.mock.calls[0][0];
    expect(body.set_id).toBe(9);
    expect(body.subject).toBe("style");
    expect(body.fit).toBe("save_memory");
    expect(body.pins).toEqual({
      "train.steps": 1800,
      "network.linear": 16,
      "train.lr": 0.0002,
      "sample.samples[*].prompt": ["{{TRIGGER_WORD}}, a harbour", "a harbour"],
    });
    expect(body).not.toHaveProperty("config_overrides");
  });

  it("sends a learning rate that is not a number as typed, for the server to refuse", async () => {
    const store = useTrainingStore();
    store.setScope({ type: "character", id: 4, name: "Grandad" });
    store.learningRate = "fast";
    store.steps = "";
    await store.startTraining();

    const body = training.createTrainingJob.mock.calls[0][0];
    expect(body.pins).toEqual({ "train.lr": "fast" });
  });

  it("sends a typed exponent learning rate as a number", async () => {
    const store = useTrainingStore();
    store.setScope({ type: "character", id: 4, name: "Grandad" });
    store.learningRate = "1e-4";
    await store.startTraining();

    expect(training.createTrainingJob.mock.calls[0][0].pins).toEqual({
      "train.lr": 0.0001,
    });
  });

  it("shows the message of a run that fits nowhere", async () => {
    const store = useTrainingStore();
    store.setScope({ type: "character", id: 4, name: "Grandad" });
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
