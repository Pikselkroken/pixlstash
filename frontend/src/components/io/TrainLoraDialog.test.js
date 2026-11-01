// The Train a LoRA dialog (#2119): the numbers each choice derives are
// printed under it, a typed value is pinned without renaming its choice, and
// Start is refused, with its reason on screen, until the run can be queued.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises, enableAutoUnmount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../../api/training", () => ({
  cancelTrainingJob: vi.fn(),
  checkAiToolkit: vi.fn(),
  checkTrainingPresets: vi.fn(),
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
    ai_toolkit_configured: true,
  })),
  patchUserConfig: vi.fn(async () => ({})),
}));
vi.mock("../../api/characters", () => ({
  characterThumbnailUrl: (id) => `/api/v1/characters/${id}/thumbnail`,
  listCharacters: vi.fn(async () => [
    { id: 4, name: "Grandad", image_count: 140 },
  ]),
}));
vi.mock("../../api/pictureSets", () => ({
  pictureSetThumbnailUrl: (id) => `/api/v1/picture_sets/${id}/thumbnail`,
  listPictureSets: vi.fn(async () => [
    { id: 9, name: "Pub nights", picture_count: 112 },
    // Grandad's reference set trains as Grandad, who is listed already.
    { id: 40, name: "Grandad refs", picture_count: 24, reference_character: { id: 4 } },
  ]),
}));
vi.mock("../../api/projects", () => ({ listProjects: vi.fn(async () => []) }));
const push = vi.fn();
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import * as training from "../../api/training";
import * as config from "../../api/config";
import * as pictureSets from "../../api/pictureSets";
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

// A test's dialog and its store's debounced resolve must not outlive it: a
// new Pinia does not stop the old store's timer, and on a slow runner it
// fired inside the next test (#2220).
enableAutoUnmount(afterEach);
afterEach(() => useTrainingStore().$dispose());

describe("TrainLoraDialog", () => {
  it("opened on a set under a person, trains them on that set first (#2124)", async () => {
    const { wrapper } = await openDialog(
      { type: "set", id: 12, name: "LoRA v3" },
      dataset({
        set_id: 12,
        count: 32,
        character: { ...GRANDAD, child_set: { id: 12, name: "LoRA v3", count: 32 } },
      }),
    );
    expect(wrapper.find('[data-testid="tld-lead"]').text()).toContain(
      "Character LoRA of Grandad",
    );
    const seg = wrapper.find(".seg").findAll("button").map((b) => b.text());
    expect(seg).toEqual([
      "LoRA v3 · 32",
      "Reference set · 24",
      "Every picture of Grandad · 140",
    ]);
  });

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
    expect(wrapper.find('[data-testid="tld-foot-text"]').text()).toBe(
      "Other GPU work pauses while it runs.",
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

  it("refuses Start while the plan on screen answers choices already changed (#2295)", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(startButton(wrapper).attributes("disabled")).toBeUndefined();
    // The change is on screen before the debounced resolve has run.
    store.effort = "strong";
    await flushPromises();
    expect(startButton(wrapper).attributes("disabled")).toBeDefined();

    await store.resolvePlan();
    await flushPromises();
    expect(startButton(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("refuses Start while the dataset is still loading (#2295)", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    store.datasetLoading = true;
    await flushPromises();
    expect(startButton(wrapper).attributes("disabled")).toBeDefined();

    store.datasetLoading = false;
    await flushPromises();
    expect(startButton(wrapper).attributes("disabled")).toBeUndefined();
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

  it("never starts a run from Enter in an Advanced field", async () => {
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    await wrapper.find('[data-testid="tld-advanced"]').trigger("click");
    const enter = () =>
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    // AppDialog's Enter-accepts skips an event already handled.
    const inField = enter();
    wrapper.find('input[aria-label="Steps"]').element.dispatchEvent(inField);
    expect(inField.defaultPrevented).toBe(true);
    const inTrigger = enter();
    wrapper.find('input[aria-label="Trigger word"]').element.dispatchEvent(inTrigger);
    expect(inTrigger.defaultPrevented).toBe(false);
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

describe("keeping the description generic", () => {
  const KEEP = "train.diff_output_preservation";
  const CLASS = "train.diff_output_preservation_class";

  beforeEach(() => {
    training.getTrainingSamplePrompts.mockResolvedValue({
      prompts: [
        { prompt: `${T} reading`, with_trigger: `${T} reading`, without_trigger: "an elderly man reading" },
        { prompt: "an elderly man reading", with_trigger: `${T} reading`, without_trigger: "an elderly man reading" },
        { prompt: `${T} at a cafe`, with_trigger: `${T} at a cafe`, without_trigger: "a young woman at a cafe" },
        { prompt: "a young woman at a cafe", with_trigger: `${T} at a cafe`, without_trigger: "a young woman at a cafe" },
      ],
      description: "an elderly man",
      description_source: "captions",
      stranger: "a young woman",
      description_count: 24,
      caption_count: 24,
      description_example: "grandad, an elderly man with glasses on a bench",
      keep_generic: true,
    });
    training.resolveTraining.mockImplementation(async (body) => {
      const p = plan();
      const on = body.pins?.[KEEP] ?? true;
      p.resolution.values[KEEP] = { value: on, source: KEEP in (body.pins || {}) ? "pinned" : "captions" };
      p.resolution.values[CLASS] = { value: "an elderly man", source: "captions" };
      p.derived[KEEP] = { value: true, source: "captions" };
      return p;
    });
  });

  function keepSwitch(wrapper) {
    return wrapper
      .findAllComponents({ name: "VSwitch" })
      .find((c) => c.attributes("data-testid") === "tld-keep-switch");
  }

  it("says most captions share the description, and the switch is on", async () => {
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    // The resolve reads the same captions the prompts were written from.
    expect(training.resolveTraining).toHaveBeenLastCalledWith(
      expect.objectContaining({ set_id: 40, character_id: 4, trigger_word: "grandad" }),
    );
    const notice = wrapper.find('[data-testid="tld-keep-generic"]');
    expect(notice.text()).toContain("All 24 captions call Grandad “an elderly man”.");
    expect(notice.text()).toContain("every elderly man is Grandad");
    expect(notice.find("mark").text()).toBe("an elderly man");
    expect(notice.text()).toContain("Keep “an elderly man” generic");
    expect(wrapper.find('[data-testid="tld-keep-source"]').text()).toBe(
      "On because 24 of 24 captions use it",
    );
    expect(keepSwitch(wrapper).attributes("model-value")).toBe("true");
  });

  it("turning it off pins it, and the lookalike is then expected to drift", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    await wrapper.find('[data-testid="tld-advanced"]').trigger("click");
    const roles = () =>
      wrapper
        .findAll('[data-testid="tld-prompt-role"]')
        .map((r) => r.findAll("span").map((t) => t.text()).join(" "));
    expect(roles()).toEqual([
      "Like him Should not change: kept generic",
      "Someone else Should not change",
    ]);

    keepSwitch(wrapper).vm.$emit("update:modelValue", false);
    await store.resolvePlan();
    await flushPromises();
    expect(store.pins[KEEP]).toBe(false);
    expect(wrapper.find('[data-testid="tld-keep-source"]').text()).toBe("Set by you");
    expect(roles()[0]).toBe("Like him Expected to drift: the words that describe him");
  });

  it("asks nothing when most captions do not share it", async () => {
    training.getTrainingSamplePrompts.mockResolvedValue({
      prompts: [],
      description: "an elderly man",
      description_source: "captions",
      stranger: "a young woman",
      description_count: 1,
      caption_count: 24,
      keep_generic: false,
    });
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(wrapper.find('[data-testid="tld-keep-generic"]').exists()).toBe(false);
  });
});

describe("a recipe that trains on clips (#2179)", () => {
  const AUDIO = "datasets[*].do_audio";

  beforeEach(() => {
    training.listTrainingPresets.mockResolvedValue([
      { id: "zimage-turbo", label: "Z-Image Turbo", offered: true, clips: false },
      { id: "minimax-h3-motion", label: "MiniMax H3 · motion", offered: true, clips: true },
    ]);
    training.resolveTraining.mockImplementation(async (body) => {
      const out = plan();
      if (body.preset === "minimax-h3-motion") {
        const sound = body.pins?.[AUDIO] ?? true;
        out.resolution.values[AUDIO] = {
          value: sound,
          source: AUDIO in (body.pins || {}) ? "pinned" : "model",
        };
        out.derived[AUDIO] = { value: true, source: "model" };
        out.resolution.values["model.quantize"] = { value: true, source: "gpu" };
      }
      return out;
    });
  });

  it("counts the clips, asks about their sound, and pins the answer", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset({ count: 20, clips: 4 }),
    );
    // A stills recipe leaves the clips out, says so, and asks nothing.
    expect(store.pictureCount).toBe(20);
    expect(wrapper.find('[data-testid="tld-checks"]').text()).toContain(
      "4 video clips are left out",
    );
    expect(wrapper.find('[data-testid="tld-sound"]').exists()).toBe(false);

    store.selectedPreset = "minimax-h3-motion";
    await store.resolvePlan();
    await flushPromises();
    expect(store.pictureCount).toBe(24);
    expect(training.resolveTraining).toHaveBeenCalledWith(
      expect.objectContaining({ preset: "minimax-h3-motion", picture_count: 24 }),
    );
    expect(wrapper.find('[data-testid="tld-checks"]').exists()).toBe(false);
    const sound = wrapper.find('[data-testid="tld-sound"]');
    expect(sound.text()).toContain("learns how they sound");

    const silent = sound.findAll("button").find((b) => b.text() === "Picture only");
    await silent.trigger("click");
    expect(store.pins[AUDIO]).toBe(false);
    await store.resolvePlan();
    await flushPromises();
    expect(wrapper.find('[data-testid="tld-sound"]').text()).toContain(
      "without their soundtrack",
    );
    // Picking the recipe's own answer again is no pin.
    const on = wrapper
      .find('[data-testid="tld-sound"]')
      .findAll("button")
      .find((b) => b.text() === "Learn the sound too");
    await on.trigger("click");
    expect(AUDIO in store.pins).toBe(false);

    // H3 ships 8-bit only: best quality cannot mean full precision there.
    store.fit = "best_quality";
    await store.resolvePlan();
    await flushPromises();
    expect(wrapper.text()).toContain("weights come in 8-bit already");
    expect(wrapper.text()).not.toContain("full precision");
  });

  it("drops the Sound answer when a stills recipe is picked", async () => {
    const { store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset({ count: 20, clips: 4 }),
    );
    store.selectedPreset = "minimax-h3-motion";
    await flushPromises();
    store.setPin(AUDIO, false);
    store.selectedPreset = "zimage-turbo";
    await flushPromises();
    expect(AUDIO in store.allPins).toBe(false);
  });

  it("explains a set of clips alone to a stills recipe, and counts them for a clip one", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset({
        count: 0,
        clips: 3,
        character: { ...GRANDAD, reference_count: 0, reference_clips: 3, all_clips: 5 },
      }),
    );
    expect(wrapper.find('[data-testid="tld-checks"]').text()).toContain(
      "no still pictures to train on. This set's 3 video clips train only with a motion recipe",
    );
    store.selectedPreset = "minimax-h3-motion";
    await store.resolvePlan();
    await flushPromises();
    expect(wrapper.text()).toContain("Reference set · 3");
    expect(wrapper.text()).toContain("Every picture of Grandad · 145");
  });

  it("says a clip recipe on a set without clips learns no motion", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset({ count: 20, clips: 0 }),
    );
    store.selectedPreset = "minimax-h3-motion";
    await store.resolvePlan();
    await flushPromises();
    expect(wrapper.find('[data-testid="tld-checks"]').text()).toContain(
      "no video clips here",
    );
  });
});

describe("opened from the Models workspace (#2169)", () => {
  it("asks what to train on, then reads the pick as if opened on it", async () => {
    const wrapper = mount(TrainLoraDialog, globalOpts);
    const store = useTrainingStore();
    store.openToPick();
    await flushPromises();

    const picker = wrapper.find('[data-testid="tld-train-on"]');
    const names = picker.findAll('[role="radio"]').map((b) => b.text());
    expect(names).toEqual(["GrandadPerson · 140 pictures", "Pub nightsSet · 112 pictures"]);
    expect(wrapper.find("footer").text()).toContain(
      "Pick a person or a set to train on.",
    );
    expect(startButton(wrapper).attributes("disabled")).toBeDefined();

    training.getTrainingDataset.mockResolvedValue(
      dataset({ set_id: 9, character_id: null, character: null, count: 112 }),
    );
    await wrapper.find('[data-testid="tld-source-set:9"]').trigger("click");
    await flushPromises();
    expect(training.getTrainingDataset).toHaveBeenCalledWith({ set_id: 9 });
    expect(store.pickable).toBe(true);
    expect(
      wrapper.find('[data-testid="tld-source-set:9"]').attributes("aria-checked"),
    ).toBe("true");
    // A set asks what it is of, as it does from the library.
    expect(wrapper.find('[data-testid="tld-subject-style"]').exists()).toBe(true);

    training.getTrainingDataset.mockResolvedValue(dataset());
    await wrapper.find('[data-testid="tld-source-character:4"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="tld-lead"]').text()).toContain(
      "Character LoRA of Grandad",
    );
  });

  it("draws a glyph, never a broken image, for a set or face with no thumbnail", async () => {
    pictureSets.listPictureSets.mockResolvedValueOnce([
      { id: 9, name: "Pub nights", picture_count: 112 },
      { id: 12, name: "Empty", picture_count: 0 },
    ]);
    const wrapper = mount(TrainLoraDialog, globalOpts);
    useTrainingStore().openToPick();
    await flushPromises();

    const tile = (id) => wrapper.find(`[data-testid="tld-source-${id}"]`);
    // An empty set has no thumbnail to ask for: the route answers 404.
    expect(tile("set:12").find("img").exists()).toBe(false);
    expect(tile("set:12").find(".tld-source__img--blank").exists()).toBe(true);
    expect(tile("set:9").find("img").attributes("src")).toBe(
      "/api/v1/picture_sets/9/thumbnail",
    );
    // A thumbnail that fails to load is swapped for the glyph.
    await tile("character:4").find("img").trigger("error");
    expect(tile("character:4").find("img").exists()).toBe(false);
    expect(tile("character:4").find(".tld-source__img--blank").exists()).toBe(true);
    expect(tile("set:9").find("img").exists()).toBe(true);

    // Picked, the person's face leads the dialog, with the same fallback.
    training.getTrainingDataset.mockResolvedValue(dataset());
    await tile("character:4").trigger("click");
    await flushPromises();
    const lead = wrapper.find('[data-testid="tld-lead"]');
    expect(lead.find("img").exists()).toBe(false);
    expect(lead.find(".tld-source__img--blank").exists()).toBe(true);

    // The dialog stays mounted: the next open retries the failed face.
    useTrainingStore().closeDialog();
    await flushPromises();
    useTrainingStore().openToPick();
    await flushPromises();
    expect(tile("character:4").find("img").exists()).toBe(true);
  });

  it("lands on Training runs when Start is pressed from there", async () => {
    const wrapper = mount(TrainLoraDialog, globalOpts);
    const store = useTrainingStore();
    store.openToPick();
    training.getTrainingDataset.mockResolvedValue(dataset());
    training.listTrainingJobs.mockResolvedValue([]);
    await store.pickScope({ type: "character", id: 4, name: "Grandad" });
    await store.resolvePlan();
    await flushPromises();

    await startButton(wrapper).trigger("click");
    await flushPromises();
    expect(training.createTrainingJob).toHaveBeenCalled();
    expect(push).toHaveBeenCalledWith({ name: "models-runs" });
    expect(training.listTrainingJobs).toHaveBeenCalled();
  });

  it("stays where it is when opened on a set or person elsewhere", async () => {
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(wrapper.find('[data-testid="tld-train-on"]').exists()).toBe(false);
    await startButton(wrapper).trigger("click");
    await flushPromises();
    expect(training.createTrainingJob).toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("memory and speed measured on this machine (#2120)", () => {
  it("gives the time and the measured peak once the setup is measured", async () => {
    training.resolveTraining.mockImplementation(async () => {
      const measured = plan();
      measured.resolution = {
        ...measured.resolution,
        evidence: "measured",
        vram_mb: 12288,
        previews_deferred: true,
        estimate: { seconds: 3000, basis: "measured" },
      };
      return measured;
    });
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(wrapper.find('[data-testid="tld-fit-readout"]').text()).toBe(
      "8-bit model · text encoder on the GPU · peaks at 12 GB, measured · previews after training",
    );
    expect(wrapper.find('[data-testid="tld-foot-text"]').text()).toBe(
      "About 50 min. Other GPU work pauses while it runs.",
    );
  });

  it("says a first run checks memory and speed before the time is known", async () => {
    training.resolveTraining.mockImplementation(async () => {
      const unmeasured = plan();
      unmeasured.resolution = {
        ...unmeasured.resolution,
        probe: true,
        estimate: { seconds: null, basis: "none" },
      };
      return unmeasured;
    });
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(wrapper.find('[data-testid="tld-foot-text"]').text()).toBe(
      "It checks memory and speed first, so the time is not known yet. " +
        "Other GPU work pauses while it runs.",
    );
  });
});

describe("the Previews question (#2415)", () => {
  function previews(pins = {}) {
    const selected = pins["train.disable_sampling"]
      ? "after"
      : pins["sample.width"] === 768
        ? "smaller"
        : "while";
    return {
      preset: "zimage-turbo",
      selected,
      full_size: [1024, 1024],
      smaller_size: 768,
      trains_same: true,
      differs: [],
      options: [
        { id: "while", layers_in_ram: 60, seconds: 5640, peak_vram_mb: 23347, free_mb: 819, tight: false, basis: "measured" },
        { id: "smaller", layers_in_ram: 0, seconds: 3360, peak_vram_mb: 23757, free_mb: 409, tight: true, basis: "estimated" },
        { id: "after", layers_in_ram: 0, seconds: 3420, peak_vram_mb: 21914, free_mb: 2252, tight: false, basis: "measured" },
      ],
    };
  }

  beforeEach(() => {
    localStorage.clear();
    training.resolveTraining.mockImplementation(async (body) => ({
      ...plan(),
      previews: previews(body.pins || {}),
    }));
  });

  const card = (wrapper, id) => wrapper.find(`[data-testid="tld-previews-${id}"]`);

  it("prices each route and starts on previews while training", async () => {
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(card(wrapper, "while").attributes("aria-checked")).toBe("true");
    expect(card(wrapper, "while").text()).toContain("1 h 34 min");
    expect(card(wrapper, "while").text()).toContain("60% of the model waits in RAM");
    expect(card(wrapper, "smaller").text()).toContain("56 min · 38 min sooner");
    expect(card(wrapper, "smaller").text()).toContain("≈23.2 GB peak · 0.4 free");
    expect(card(wrapper, "smaller").text()).toContain("Estimated, scaled from 1024²");
    expect(card(wrapper, "smaller").text()).toContain("Tight on this GPU");
    expect(card(wrapper, "after").text()).toContain("21.4 GB peak · 2.2 free");
    expect(card(wrapper, "after").text()).not.toContain("Tight");
    expect(wrapper.find('[data-testid="tld-previews"]').text()).toContain(
      "The LoRA trains the same either way.",
    );
  });

  it("writes each route's pins, and remembers a pick for the next opening", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    await card(wrapper, "smaller").trigger("click");
    expect(store.pins).toEqual({ "sample.width": 768, "sample.height": 768 });
    await store.resolvePlan();
    await flushPromises();
    await card(wrapper, "after").trigger("click");
    expect(store.pins).toEqual({ "train.disable_sampling": true });
    await store.resolvePlan();
    await flushPromises();
    expect(card(wrapper, "after").attributes("aria-checked")).toBe("true");
    expect(wrapper.find('[data-testid="tld-previews"]').text()).toContain("1 pinned");

    store.closeDialog();
    await flushPromises();
    await store.openForScope({ type: "character", id: 4, name: "Grandad" });
    expect(store.pins).toEqual({});
    await store.resolvePlan();
    await flushPromises();
    expect(store.pins).toEqual({ "train.disable_sampling": true });

    // Back to the default forgets it.
    await card(wrapper, "while").trigger("click");
    expect(store.pins).toEqual({});
    store.closeDialog();
    await flushPromises();
    await store.openForScope({ type: "character", id: 4, name: "Grandad" });
    await store.resolvePlan();
    await flushPromises();
    expect(store.pins).toEqual({});
  });

  it("keeps a typed 'previews after training: no' when going back to the default", async () => {
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    store.setPin("train.disable_sampling", false);
    await store.resolvePlan();
    await flushPromises();
    await card(wrapper, "while").trigger("click");
    expect(store.pins).toEqual({ "train.disable_sampling": false });
  });

  it("never puts a remembered pick over a value typed before it was asked", async () => {
    localStorage.setItem(
      "pixlstash.train.previews",
      JSON.stringify({ "zimage-turbo": "after" }),
    );
    // Asked only once the typed pin is in (as when a later resolve is the
    // first one measured).
    training.resolveTraining.mockImplementation(async (body) => ({
      ...plan(),
      previews: body.pins && "train.disable_sampling" in body.pins ? previews(body.pins) : null,
    }));
    const { wrapper, store } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    store.setPin("train.disable_sampling", false);
    await store.resolvePlan();
    await flushPromises();
    expect(card(wrapper, "while").exists()).toBe(true);
    expect(store.pins).toEqual({ "train.disable_sampling": false });
  });

  it("is not asked when the server has no question", async () => {
    training.resolveTraining.mockImplementation(async () => ({ ...plan(), previews: null }));
    const { wrapper } = await openDialog(
      { type: "character", id: 4, name: "Grandad" },
      dataset(),
    );
    expect(wrapper.find('[data-testid="tld-previews"]').exists()).toBe(false);
  });
});

describe("recipe updates from pixlstash.dev (#2122)", () => {
  const SCOPE = { type: "character", id: 4, name: "Grandad" };

  it("checks on the button, and asks once whether to check every day", async () => {
    training.checkTrainingPresets.mockResolvedValue({
      checked: true,
      updated: [],
      error: null,
    });
    const { wrapper } = await openDialog(SCOPE, dataset());
    // Not opted in: opening the dialog fetches nothing.
    expect(training.checkTrainingPresets).not.toHaveBeenCalled();

    await wrapper.find('[data-testid="tld-check-presets"]').trigger("click");
    await flushPromises();
    expect(training.checkTrainingPresets).toHaveBeenCalledWith(false);
    expect(wrapper.find('[data-testid="tld-check-presets-status"]').text()).toBe(
      "Your recipes are up to date.",
    );
    const ask = wrapper.find('[data-testid="tld-auto-check-ask"]');
    expect(ask.text()).toContain("Check for recipe updates automatically, once a day?");

    const no = ask.findAll("button").find((b) => b.text() === "No");
    await no.trigger("click");
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      training_presets_auto_check: false,
    });
    expect(wrapper.find('[data-testid="tld-auto-check-note"]').text()).toBe(
      "You can turn on automatic checks any time in Settings › AI-toolkit.",
    );

    // Answered: a second check does not ask again.
    await wrapper.find('[data-testid="tld-check-presets"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="tld-auto-check-ask"]').exists()).toBe(false);
  });

  it("runs the opted-in check on open and lists the updated recipe", async () => {
    config.getUserConfig.mockResolvedValueOnce({
      ai_toolkit_repo_path: "/home/me/ai-toolkit",
      ai_toolkit_python: "/home/me/ai-toolkit/venv/bin/python",
      ai_toolkit_configured: true,
      training_presets_auto_check: true,
    });
    training.checkTrainingPresets.mockResolvedValue({
      checked: true,
      updated: [{ id: "zimage-turbo", label: "Z-Image Turbo", version: 2 }],
      error: null,
    });
    const { wrapper } = await openDialog(SCOPE, dataset());
    await flushPromises();
    expect(training.checkTrainingPresets).toHaveBeenCalledWith(true);
    expect(training.listTrainingPresets).toHaveBeenCalledTimes(2);
    expect(wrapper.find('[data-testid="tld-check-presets-status"]').text()).toBe(
      "Updated Z-Image Turbo (version 2).",
    );
  });

  it("says an unreachable site changed nothing", async () => {
    training.checkTrainingPresets.mockResolvedValue({
      checked: true,
      updated: [],
      error: "Could not read the recipe list from pixlstash.dev.",
    });
    const { wrapper } = await openDialog(SCOPE, dataset());
    await wrapper.find('[data-testid="tld-check-presets"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="tld-check-presets-status"]').text()).toBe(
      "Could not read the recipe list from pixlstash.dev. Your recipes are unchanged.",
    );
  });
});
