// The Train a LoRA dialog (#2119): the numbers each choice derives are
// printed under it, a typed value is pinned without renaming its choice, and
// Start is refused, with its reason on screen, until the run can be queued.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../../api/training", () => ({
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
vi.mock("../../api/config", () => ({
  getUserConfig: vi.fn(async () => ({
    ai_toolkit_repo_path: "/home/me/ai-toolkit",
    ai_toolkit_python: "/home/me/ai-toolkit/venv/bin/python",
  })),
}));
vi.mock("../../api/characters", () => ({
  characterThumbnailUrl: (id) => `/api/v1/characters/${id}/thumbnail`,
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import * as training from "../../api/training";
import { useTrainingStore } from "../../stores/useTrainingStore";
import TrainLoraDialog from "./TrainLoraDialog.vue";

const T = "{{TRIGGER_WORD}}";
const GRANDAD = {
  id: 4,
  name: "Grandad",
  reference_set_id: 40,
  reference_count: 24,
  all_count: 140,
};

function plan({ steps = { value: 2400, source: "pictures" } } = {}) {
  return {
    resolution: {
      profile_label: "8-bit model · text encoder on the GPU",
      vram_mb: 16000,
      values: {
        "train.steps": steps,
        "train.lr": { value: 0.0001, source: "effort" },
        "network.linear": { value: 32, source: "effort" },
        "network.linear_alpha": { value: 32, source: "effort" },
        "model.qtype": { value: "qfloat8", source: "gpu" },
      },
    },
    derived: {
      "train.steps": { value: 2400, source: "pictures" },
      "train.lr": { value: 0.0001, source: "effort" },
    },
    rules: {
      "train.steps": { kind: "int", lo: 1, hi: 100000, count: 0, choices: [] },
      "train.lr": { kind: "float", lo: 1e-7, hi: 0.1, count: 0, choices: [] },
      "network.linear": { kind: "int", lo: 1, hi: 512, count: 0, choices: [] },
      "network.linear_alpha": { kind: "float", lo: 0, hi: 1024, count: 0, choices: [] },
      "model.qtype": { kind: "enum", lo: 0, hi: 0, count: 0, choices: ["qfloat8", "qint8"] },
    },
    steps_rule: { steps_per_picture: 100, min_steps: 1200, max_steps: 3000 },
    room: { vram_mb: 20480, ram_mb: 60000 },
    presets: [
      { id: "zimage-turbo", label: "Z-Image Turbo", offered: true, verdict: "fits" },
    ],
  };
}

function dataset(over = {}) {
  return {
    set_id: 40,
    character_id: 4,
    count: 24,
    min_pictures: 6,
    untagged: 0,
    near_duplicates: 0,
    other_people: 0,
    character: GRANDAD,
    ...over,
  };
}

const globalOpts = {
  global: {
    stubs: {
      AppDialog: {
        name: "AppDialog",
        props: ["open", "title", "subtitle"],
        emits: ["close", "accept"],
        template:
          "<div v-if='open'><h2>{{ title }}</h2><span class='sub'>{{ subtitle }}</span><slot /><footer><slot name='footer' /></footer></div>",
      },
      AppSelect: true,
      AppInput: {
        props: ["modelValue", "ariaLabel", "error"],
        emits: ["update:modelValue", "blur"],
        template:
          "<input :aria-label='ariaLabel' :value='modelValue' @input=\"$emit('update:modelValue', $event.target.value)\" />",
      },
      AppButton: {
        props: ["disabled", "loading"],
        template: "<button :disabled='disabled || loading'><slot /></button>",
      },
      Segmented: {
        props: ["options", "modelValue"],
        emits: ["update:modelValue"],
        template:
          "<div class='seg'><button v-for='o in options' :key='o.id' type='button' :disabled='o.disabled' @click=\"$emit('update:modelValue', o.id)\">{{ o.label }}</button></div>",
      },
    },
  },
};

async function openDialog(scope, ds) {
  training.getTrainingDataset.mockResolvedValue(ds);
  const wrapper = mount(TrainLoraDialog, globalOpts);
  const store = useTrainingStore();
  await store.openForScope(scope);
  await store.resolvePlan();
  await flushPromises();
  return { wrapper, store };
}

function startButton(wrapper) {
  return wrapper.find('[data-testid="tld-start"]');
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  training.listTrainingPresets.mockResolvedValue([
    { id: "zimage-turbo", label: "Z-Image Turbo", offered: true },
  ]);
  training.resolveTraining.mockImplementation(async (body) =>
    plan(
      body.pins?.["train.steps"]
        ? { steps: { value: body.pins["train.steps"], source: "pinned" } }
        : {},
    ),
  );
  training.getTrainingSamplePrompts.mockResolvedValue({
    prompts: [
      { prompt: `${T} by the sea`, with_trigger: `${T} by the sea`, without_trigger: "a man by the sea" },
      { prompt: "a man by the sea", with_trigger: `${T} by the sea`, without_trigger: "a man by the sea" },
    ],
  });
  training.createTrainingJob.mockResolvedValue({ id: 1 });
});

describe("TrainLoraDialog", () => {
  it("leads with the person and prints what each choice set", async () => {
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(wrapper.find(".sub").text()).toBe("Grandad · 24 pictures");
    expect(wrapper.find('[data-testid="tld-lead"]').text()).toContain(
      "Character LoRA of Grandad",
    );
    expect(wrapper.text()).toContain("Reference set · 24");
    expect(wrapper.text()).toContain("Every picture of Grandad · 140");
    expect(wrapper.find('[data-testid="tld-effort-readout"]').text()).toBe(
      "2,400 steps (about 100 per picture) · rank 32 · LR 0.0001",
    );
    expect(wrapper.find('[data-testid="tld-fit-readout"]').text()).toBe(
      "8-bit model · text encoder on the GPU · peaks near 16 GB",
    );
    expect(wrapper.text()).toContain("Auto · 20 GB free");
    expect(wrapper.text()).toContain(
      "From the character's name. Goes into 1 of the 2 sample prompts.",
    );
    // No subject cards for a person.
    expect(wrapper.find('[data-testid="tld-subject-person"]').exists()).toBe(false);

    await startButton(wrapper).trigger("click");
    await flushPromises();
    expect(training.createTrainingJob).toHaveBeenCalledWith(
      expect.objectContaining({ set_id: 40, character_id: 4, trigger_word: "grandad" }),
    );
  });

  it("asks a set what it is of, and refuses Start until it is answered", async () => {
    const { wrapper, store } = await openDialog(
      { type: "set", id: 9, name: "Pub nights" },
      dataset({ set_id: 9, character_id: null, character: null, count: 112 }),
    );
    expect(startButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.find("footer").text()).toContain("Say what the set is of.");
    expect(training.resolveTraining).not.toHaveBeenCalled();

    await wrapper.find('[data-testid="tld-subject-style"]').trigger("click");
    expect(store.subject).toBe("style");
    await store.resolvePlan();
    await flushPromises();
    expect(startButton(wrapper).attributes("disabled")).toBeUndefined();
    expect(wrapper.text()).toContain("From the set's name.");
  });

  it("pins a typed value, keeps the choice's name, and unpins back", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    await wrapper.find('[data-testid="tld-advanced"]').trigger("click");
    const steps = wrapper.find('input[aria-label="Steps"]');
    expect(steps.element.value).toBe("2400");
    await steps.setValue("2000");
    expect(store.pins).toEqual({ "train.steps": 2000 });
    await store.resolvePlan();
    await flushPromises();

    const row = wrapper.find('[data-testid="tld-row-train.steps"]');
    expect(row.text()).toContain("Typed by you, so it stays put");
    expect(wrapper.text()).toContain("1 pinned by you");
    expect(wrapper.find('[data-testid="tld-effort-readout"]').text()).toMatch(
      /^2,000 steps \(pinned\)/,
    );
    // Effort keeps its name; its label offers the unpin.
    const unpin = wrapper
      .findAll("button")
      .find((b) => b.text().endsWith("1 pinned · unpin"));
    expect(unpin).toBeTruthy();
    await unpin.trigger("click");
    expect(store.pins).toEqual({});

    // An out-of-range value is not a pin and blocks Start with its reason.
    await steps.setValue("0");
    expect(store.pins).toEqual({});
    expect(wrapper.find("footer").text()).toContain("Fix the Advanced value");
    expect(startButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("edits the sample prompts and keeps at least one", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    await wrapper.find('[data-testid="tld-advanced"]').trigger("click");
    expect(wrapper.findAll('[data-testid="tld-prompt"]')).toHaveLength(2);
    expect(wrapper.text()).toContain("2 · 1 with the trigger word");

    await wrapper.find('button[aria-label="Add grandad to this prompt"]').trigger("click");
    expect(wrapper.text()).toContain("2 · 2 with the trigger word");
    expect(wrapper.text()).toContain("Goes into 2 of the 2 sample prompts.");

    await wrapper.find('button[aria-label="Remove sample prompt 2"]').trigger("click");
    expect(store.prompts).toHaveLength(1);
    expect(store.promptsPinned).toBe(true);
    expect(
      wrapper.find('button[aria-label="Remove sample prompt 1"]').attributes("disabled"),
    ).toBeDefined();
  });

  it("warns before Start: too few, untagged, near copies, other people", async () => {
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset({ count: 4, untagged: 2, near_duplicates: 2, other_people: 1 }),
    );
    const checks = wrapper.find('[data-testid="tld-checks"]').text();
    expect(checks).toContain("Only 4 pictures");
    expect(checks).toContain("2 pictures have no tags yet");
    expect(checks).toContain("2 pictures are near copies");
    expect(checks).toContain("1 picture shows somebody else's face");
    // Warnings, not blocks.
    expect(startButton(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("says why a run cannot fit and refuses Start", async () => {
    training.resolveTraining.mockResolvedValue({
      ...plan(),
      resolution: null,
      error: { message: "Z-Image Turbo needs 10.5 GB of GPU memory at least" },
    });
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(wrapper.find('[data-testid="tld-plan-error"]').text()).toContain(
      "needs 10.5 GB",
    );
    expect(startButton(wrapper).attributes("disabled")).toBeDefined();
  });
});
