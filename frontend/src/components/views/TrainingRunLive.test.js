// One training run's page (#2118): the step axis, the cells filling in, the
// cover, and the figures that say where they came from.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async (importOriginal) => ({
  ...(await importOriginal()),
  VTooltip: {
    name: "VTooltip",
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

const getTrainingJobLive = vi.fn();
const setTrainingJobCover = vi.fn();
const cancelTrainingJob = vi.fn();
vi.mock("../../api/training", () => ({
  getTrainingJobLive: (...args) => getTrainingJobLive(...args),
  setTrainingJobCover: (...args) => setTrainingJobCover(...args),
  cancelTrainingJob: (...args) => cancelTrainingJob(...args),
}));
const copyText = vi.fn();
vi.mock("../../utils/clipboard", () => ({
  copyText: (...args) => copyText(...args),
}));
vi.mock("../../api/modelImports", () => ({
  runSampleUrl: (folderId, runName, filename) =>
    `/samples/${folderId}/${runName}/${filename}`,
}));


import TrainingRunLive from "./TrainingRunLive.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";

const stubs = {
  "v-icon": true,
  LoraLossChart: { template: "<div class='chart-stub' />" },
  AppBarButton: {
    props: ["ariaLabel"],
    template: "<button :aria-label='ariaLabel'><slot /></button>",
  },
  AppButton: {
    props: ["disabled", "loading"],
    template: "<button class='app-btn' :disabled='disabled'><slot /></button>",
  },
  TrainingSampleDialog: {
    props: ["sample"],
    template: "<div class='tsd-stub' :data-sample='JSON.stringify(sample)' />",
  },
};

const cells = (states) =>
  states.map((state, i) => ({
    state,
    filename: state === "sample" ? `s_${i}.jpg` : null,
  }));

function payload(overrides = {}) {
  return {
    id: 7,
    uuid: "abcd",
    name: "ohwx-portrait-v2",
    status: "running",
    phase: "previews",
    previews: { drawing: 2, total: 2 },
    previews_deferred: false,
    step: 1000,
    total_steps: 1500,
    set_id: null,
    character_id: null,
    trigger_word: "ohwx",
    phases: [
      { phase: "waiting_gpu", seconds: 30 },
      { phase: "loading", seconds: 125 },
      { phase: "training", seconds: 600 },
    ],
    columns: [0, 250, 500, 750, 1000, 1250, 1500],
    rows: [
      {
        index: 0,
        prompt: "photo of ohwx woman",
        with_trigger: true,
        cells: cells(["sample", "sample", "sample", "sample", "sample", "future", "future"]),
      },
      {
        index: 1,
        prompt: "photo of a woman",
        with_trigger: false,
        cells: cells(["sample", "sample", "sample", "sample", "drawing", "future", "future"]),
      },
    ],
    run_folder: { folder_id: 3, run_name: "ohwx_abcd" },
    imported: false,
    coverable: [750, 1000, 1500],
    cover_step: 750,
    loss_curve: [],
    dataset_picture_ids: [1, 2, 3],
    streamed: null,
    speed: { seconds_per_step: 1.9, remaining_low_s: 960, remaining_high_s: 1260 },
    telemetry: {
      gpu: { total_mb: 24576, used_mb: 21811, ours_mb: null },
      ram: { total_mb: null, used_mb: null, ours_mb: null },
      need: { vram_mb: 16000, ram_mb: 16000 },
      paging: false,
    },
    waiting: null,
    finished_while_closed: null,
    error: null,
    created_at: "2026-10-26T01:00:00+00:00",
    started_at: "2026-10-26T01:00:30+00:00",
    completed_at: null,
    ...overrides,
  };
}

const mounted = [];
async function open(body) {
  getTrainingJobLive.mockResolvedValue(body);
  const wrapper = mount(TrainingRunLive, {
    props: { jobId: 7 },
    global: { stubs },
  });
  mounted.push(wrapper);
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  getTrainingJobLive.mockReset();
  setTrainingJobCover.mockReset();
  cancelTrainingJob.mockReset();
  copyText.mockReset();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  vi.restoreAllMocks();
});

describe("TrainingRunLive", () => {
  it("draws the run on one step axis as it trains", async () => {
    const wrapper = await open(payload());
    expect(getTrainingJobLive).toHaveBeenCalledWith(7);
    expect(wrapper.find(".trl-status").text()).toContain("Training 1,000 / 1,500");
    expect(wrapper.find(".trl-status").text()).toContain("Generating previews 2 of 2");
    expect(wrapper.find(".trl-setup").text()).toContain("Started training after 2:35");
    expect(wrapper.findAll(".trl-stepn").map((n) => n.text())).toEqual([
      "Step 0", "250", "500", "750", "1,000", "1,250", "1,500",
    ]);
    // The groups, the trigger word drawn as a token.
    const groups = wrapper.findAll(".trl-gline").map((g) => g.text());
    expect(groups[0]).toContain("With the trigger word");
    expect(groups[1]).toContain("Without it");
    expect(groups[1]).toContain("should change little from step 0");
    expect(wrapper.find(".trl-trig").text()).toBe("ohwx");
    // Samples point at the run folder; the one being drawn spins.
    const img = wrapper.find("img.trl-cell");
    expect(img.attributes("src")).toBe("/samples/3/ohwx_abcd/s_0.jpg");
    expect(wrapper.find(".trl-cell--gen").text()).toContain("2 of 2");
    expect(wrapper.findAll(".trl-cell--future")).toHaveLength(4);
    // The cover is the chosen column; a kept checkpoint offers itself, except
    // the one whose previews are being drawn, which says so instead.
    expect(wrapper.findAll(".trl-step")[3].text()).toContain("Cover");
    expect(wrapper.findAll(".trl-use").map((b) => b.text())).toEqual([
      "Use this step",
    ]);
  });

  it("opens a sample full size on a click", async () => {
    const wrapper = await open(payload());
    const sample = () => JSON.parse(wrapper.find(".tsd-stub").attributes("data-sample"));
    expect(sample()).toBe(null);
    // The second row's third cell: step 500 of "photo of a woman".
    const cell = wrapper.findAll("img.trl-cell")[7];
    expect(cell.attributes("src")).toBe("/samples/3/ohwx_abcd/s_2.jpg");
    await cell.trigger("click");
    expect(sample()).toEqual({
      src: "/samples/3/ohwx_abcd/s_2.jpg",
      step: 500,
      prompt: "photo of a woman",
    });
  });

  it("labels every figure, and an unreadable one is Unknown, never 0", async () => {
    const wrapper = await open(payload());
    const bar = wrapper.find(".trl-statusbar").text();
    expect(bar).toContain("21.3 / 24.0 GB");
    expect(bar).toContain("1.9 s");
    expect(bar).toContain("16–21 min");
    expect(bar).toMatch(/RAM\s*Unknown\s*Not measured/);
    expect(bar).not.toMatch(/(^|[^\d.])0(\.0)? GB/);
    expect(wrapper.findAll(".trl-prov--estimated")).toHaveLength(1);
  });

  it("makes a column the cover", async () => {
    setTrainingJobCover.mockResolvedValue({ cover_step: 1500, applied: false });
    const wrapper = await open(payload());
    getTrainingJobLive.mockResolvedValue(payload({ cover_step: 1500 }));
    await wrapper.find(".trl-use").trigger("click");
    await flushPromises();
    expect(setTrainingJobCover).toHaveBeenCalledWith(7, 1500);
    expect(wrapper.findAll(".trl-step")[6].text()).toContain("Cover");
    expect(wrapper.findAll(".trl-step")[3].text()).not.toContain("Cover");
  });

  it("says a failure once, as one sentence, with the trainer's text behind it", async () => {
    const sentence = "Ran out of GPU memory at step 500: it needed 5.06 GiB more.";
    const report = "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 5.06 GiB";
    copyText.mockResolvedValue(true);
    const wrapper = await open(
      payload({
        status: "error",
        phase: null,
        previews: null,
        step: 500,
        error: sentence,
        report,
        rows: [
          {
            index: 0,
            prompt: "photo of ohwx woman",
            with_trigger: true,
            cells: cells(["sample", "sample", "missing", "missing", "missing", "missing", "missing"]),
          },
        ],
      }),
    );
    const notice = wrapper.find(".trl-notice");
    expect(notice.find("b").text()).toBe(sentence);
    expect(wrapper.find('[data-testid="trl-full"]').exists()).toBe(false);
    await wrapper.find('[data-testid="trl-full-toggle"]').trigger("click");
    expect(wrapper.find('[data-testid="trl-full"]').text()).toBe(report);
    await wrapper.find('[data-testid="trl-full-copy"]').trigger("click");
    await flushPromises();
    expect(copyText).toHaveBeenCalledWith(report);

    // The step it broke at says so; the steps it never reached stay quiet.
    expect(wrapper.findAll(".trl-step")[2].text()).toContain("failed here");
    const empty = wrapper.findAll(".trl-cell--future").map((c) => c.text());
    expect(empty).toEqual(["not drawn", "", "", "", ""]);
    // The trained-on pictures are the rail's, and the end of the track is
    // somewhere a failed run never gets to.
    expect(wrapper.find("[aria-label='Training pictures']").exists()).toBe(false);
    expect(wrapper.find(".trl-endph").exists()).toBe(false);
  });

  it("puts 'failed here' past a step it finished drawing", async () => {
    const wrapper = await open(
      payload({
        status: "error",
        phase: null,
        previews: null,
        step: 500,
        error: "Training failed.",
        rows: [
          {
            index: 0,
            prompt: "photo of ohwx woman",
            with_trigger: true,
            cells: cells(["sample", "sample", "sample", "missing", "missing", "missing", "missing"]),
          },
        ],
      }),
    );
    const steps = wrapper.findAll(".trl-step");
    expect(steps[2].text()).not.toContain("failed here");
    expect(steps[3].text()).toContain("failed here");
  });

  it("offers no full message when the trainer said nothing more", async () => {
    const wrapper = await open(
      payload({ status: "error", phase: null, previews: null, error: "Training failed.", report: null }),
    );
    expect(wrapper.find(".trl-notice b").text()).toBe("Training failed.");
    expect(wrapper.find('[data-testid="trl-full-toggle"]').exists()).toBe(false);
  });

  it("says when the machine is short of RAM to start", async () => {
    const wrapper = await open(
      payload({
        status: "queued",
        phase: "waiting_gpu",
        step: 0,
        phases: [{ phase: "waiting_gpu", seconds: 12 }],
        waiting: { code: "ram", text: "Needs 15.6 GB of RAM; 9.8 GB is free." },
        telemetry: {
          gpu: { total_mb: 24576, used_mb: 1500, ours_mb: null },
          ram: { total_mb: 65536, used_mb: 55500, ours_mb: null },
          need: { vram_mb: 16000, ram_mb: 16000 },
          paging: false,
        },
      }),
    );
    expect(wrapper.find(".trl-notice--err").text()).toContain(
      "Not enough system RAM to start.",
    );
    expect(wrapper.find(".trl-statusbar").text()).toContain("needs 15.6 GB, 9.8 GB free");
    expect(wrapper.find(".trl-status").text()).toContain("Waiting for the GPU");
    expect(wrapper.find(".app-btn").text()).toBe("Stop training");
  });

  it("says a run finished while PixlStash was closed, and stops asking", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = await open(
        payload({
          status: "completed",
          phase: null,
          previews: null,
          step: 1500,
          imported: true,
          completed_at: "2026-10-26T09:00:00+00:00",
          finished_while_closed: { at: "2026-10-26T03:12:00+00:00" },
          speed: { seconds_per_step: 1.9, remaining_low_s: null, remaining_high_s: null },
        }),
      );
      expect(wrapper.find(".trl-notice--ok").text()).toContain(
        "Finished while PixlStash was closed",
      );
      expect(wrapper.find(".app-btn").exists()).toBe(false);
      getTrainingJobLive.mockClear();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(getTrainingJobLive).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a trained run live while it draws its previews, and offers to skip them", async () => {
    // Trained but not imported: `completed` with no completed_at (#2291).
    vi.useFakeTimers();
    try {
      const drawing = payload({
        status: "completed",
        phase: "previews",
        step: 1500,
        completed_at: null,
      });
      const wrapper = await open(drawing);
      expect(wrapper.find(".trl-status").text()).toContain("Generating previews");
      expect(wrapper.find(".trl-status").text()).not.toContain("Finished");
      expect(wrapper.find(".app-btn").text()).toBe("Skip previews");

      getTrainingJobLive.mockClear();
      await vi.advanceTimersByTimeAsync(2_500);
      expect(getTrainingJobLive).toHaveBeenCalled();

      cancelTrainingJob.mockResolvedValue({ status: "cancelled" });
      // Skipping keeps the trained model, so it does not ask as Stop does.
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      await wrapper.find(".app-btn").trigger("click");
      await flushPromises();
      expect(confirm).not.toHaveBeenCalled();
      expect(cancelTrainingJob).toHaveBeenCalledWith(7);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps an importing run live with nothing to stop", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = await open(
        payload({
          status: "completed",
          phase: "importing",
          previews: null,
          step: 1500,
          completed_at: null,
        }),
      );
      expect(wrapper.find(".trl-status").text()).toContain("Importing to the shelf");
      expect(wrapper.find(".app-btn").exists()).toBe(false);
      getTrainingJobLive.mockClear();
      await vi.advanceTimersByTimeAsync(2_500);
      expect(getTrainingJobLive).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops asking about a run this library does not hold", async () => {
    vi.useFakeTimers();
    try {
      getTrainingJobLive.mockRejectedValue(
        Object.assign(new Error("Training job not found"), {
          response: { status: 404 },
        }),
      );
      const wrapper = mount(TrainingRunLive, {
        props: { jobId: 7 },
        global: { stubs },
      });
      mounted.push(wrapper);
      await flushPromises();
      expect(wrapper.text()).toContain("That training run is not in this library.");
      getTrainingJobLive.mockClear();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(getTrainingJobLive).not.toHaveBeenCalled();

      // Another run: the old error is gone while it loads, and it is asked for.
      let answer;
      getTrainingJobLive.mockReturnValue(new Promise((r) => (answer = r)));
      await wrapper.setProps({ jobId: 8 });
      expect(wrapper.text()).not.toContain("not in this library");
      expect(getTrainingJobLive).toHaveBeenCalledWith(8);
      answer(payload({ id: 8 }));
      await flushPromises();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads Unknown when the card's total is known but its use is not", async () => {
    const wrapper = await open(
      payload({
        telemetry: {
          gpu: { total_mb: 24576, used_mb: null, ours_mb: null },
          ram: { total_mb: null, used_mb: null, ours_mb: null },
          need: { vram_mb: 16000, ram_mb: 16000 },
          paging: false,
        },
      }),
    );
    expect(wrapper.find(".trl-statusbar").text()).toMatch(/GPU\s*Unknown/);
  });

  it("says so when a run it is showing stops answering, and does not ask again", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = await open(payload());
      getTrainingJobLive.mockRejectedValue(
        Object.assign(new Error("Training job not found"), {
          response: { status: 404 },
        }),
      );
      await vi.advanceTimersByTimeAsync(2_000);
      await flushPromises();
      expect(wrapper.find(".trl-notice--err").text()).toContain("This run is gone.");
      // The last board stays up under the notice.
      expect(wrapper.find(".trl-board").exists()).toBe(true);
      getTrainingJobLive.mockClear();
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(10_000);
      expect(getTrainingJobLive).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps one poll going when it is pointed at another run", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = await open(payload());
      getTrainingJobLive.mockResolvedValue(payload({ id: 8 }));
      await vi.advanceTimersByTimeAsync(1_000);
      await wrapper.setProps({ jobId: 8 });
      await flushPromises();
      getTrainingJobLive.mockClear();
      await vi.advanceTimersByTimeAsync(10_000);
      // One read every 2 s: five, not the ten two chains would make.
      expect(getTrainingJobLive).toHaveBeenCalledTimes(5);
    } finally {
      vi.useRealTimers();
    }
  });

  it("puts prompts it could not read under their own heading", async () => {
    const base = payload();
    const wrapper = await open(
      payload({
        rows: base.rows.map((row) => ({ ...row, prompt: "", with_trigger: null })),
      }),
    );
    const groups = wrapper.findAll(".trl-gline").map((g) => g.text());
    expect(groups).toHaveLength(1);
    expect(groups[0]).toContain("Previews");
    expect(groups[0]).not.toContain("With the trigger word");
  });

  it("does not print NaN for RAM it is short of when its use is unknown", async () => {
    const wrapper = await open(
      payload({
        status: "queued",
        phase: "waiting_gpu",
        waiting: { code: "ram", text: "Needs 52.0 GB of RAM." },
        telemetry: {
          gpu: { total_mb: 24576, used_mb: 1500, ours_mb: null },
          ram: { total_mb: 65536, used_mb: null, ours_mb: null },
          need: { vram_mb: 16000, ram_mb: 53248 },
          paging: false,
        },
      }),
    );
    const bar = wrapper.find(".trl-statusbar").text();
    expect(bar).not.toContain("NaN");
    expect(bar).toMatch(/RAM\s*Unknown/);
  });

  it("polls again once a run it lost answers again", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = await open(payload());
      getTrainingJobLive.mockRejectedValueOnce(
        Object.assign(new Error("Training job not found"), {
          response: { status: 404 },
        }),
      );
      await vi.advanceTimersByTimeAsync(2_000);
      await flushPromises();
      expect(wrapper.find(".trl-notice--err").exists()).toBe(true);

      // Stop reads the run again, and it answers: polling resumes.
      cancelTrainingJob.mockResolvedValue({});
      vi.spyOn(window, "confirm").mockReturnValue(true);
      await wrapper.find(".app-btn").trigger("click");
      await flushPromises();
      expect(wrapper.find(".trl-notice--err").exists()).toBe(false);
      getTrainingJobLive.mockClear();
      await vi.advanceTimersByTimeAsync(4_000);
      expect(getTrainingJobLive).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not re-arm a poll for a read that lands after the page closed", async () => {
    vi.useFakeTimers();
    try {
      let answer;
      getTrainingJobLive.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
      const wrapper = mount(TrainingRunLive, { props: { jobId: 7 }, global: { stubs } });
      wrapper.unmount();
      answer(payload());
      await flushPromises();
      getTrainingJobLive.mockClear();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(getTrainingJobLive).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks before stopping, and stops nothing when the owner says no", async () => {
    const wrapper = await open(payload({ status: "running" }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await wrapper.find(".app-btn").trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledWith(
      'Stop training "ohwx-portrait-v2"? The run will stop and cannot be resumed.',
    );
    expect(cancelTrainingJob).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await wrapper.find(".app-btn").trigger("click");
    await flushPromises();
    expect(cancelTrainingJob).toHaveBeenCalledWith(7);
  });

  it("says a run queued behind another run waits for that run, not the GPU", async () => {
    const wrapper = await open(
      payload({
        status: "queued",
        phase: "waiting_gpu",
        step: 0,
        phases: [{ phase: "waiting_gpu", seconds: 12 }],
        waiting: { code: "fifo", text: "Waiting behind ComfyUI 'Klein inpaint'." },
      }),
    );
    expect(wrapper.find(".trl-status").text()).toContain("Waiting for the run ahead of it");
    expect(wrapper.find(".trl-status").text()).not.toContain("Waiting for the GPU");
    expect(wrapper.text()).toContain("Waiting for the run ahead of it.");
  });

  it("says it is checking memory and speed, not waiting, while its probe runs", async () => {
    const wrapper = await open(
      payload({
        status: "queued",
        phase: "checking",
        step: 0,
        phases: [{ phase: "checking", seconds: 40 }],
        waiting: null,
        downloading: true,
      }),
    );
    const status = wrapper.find(".trl-status").text();
    expect(status).toContain("Checking memory and speed");
    expect(status).not.toContain("Waiting for the GPU");
    expect(status).toContain("Downloading the model, once per model");
  });

  it("says nothing of a download when the trainer is not fetching one", async () => {
    const wrapper = await open(
      payload({ status: "running", phase: "loading", step: 0, downloading: false }),
    );
    expect(wrapper.find(".trl-status").text()).not.toContain("Downloading");
  });

  it("puts its status in the shelf's bar when it mounts with the shelf", async () => {
    // Opened from another tab: the shelf and this page mount in one pass, so
    // the bar's slot is not in the document while this page sets up.
    getTrainingJobLive.mockResolvedValue(payload());
    const Shelf = {
      components: { TrainingRunLive },
      provide: { "shelf-run-bar": true },
      template: '<div><div id="shelf-run-bar"></div><TrainingRunLive :job-id="7" /></div>',
    };
    const wrapper = mount(Shelf, { attachTo: document.body, global: { stubs } });
    mounted.push(wrapper);
    await flushPromises();
    expect(wrapper.find("#shelf-run-bar .trl-status").exists()).toBe(true);
    expect(wrapper.find(".trl > .trl-bar").exists()).toBe(false);
  });

  it("draws a run trained elsewhere from its folder, and says what it cannot show", async () => {
    const run = {
      name: "walter_v2",
      checkpoints: [
        { filename: "walter_v2_000000500.safetensors", step: 500 },
        { filename: "walter_v2.safetensors", step: null },
      ],
      samples: [
        { filename: "a_0_0.jpg", step: 0, index: 0 },
        { filename: "a_500_0.jpg", step: 500, index: 0 },
      ],
      trigger_words: ["w4lt"],
      sample_prompts: ["w4lt on a bike"],
      total_steps: 1000,
    };
    const wrapper = mount(TrainingRunLive, {
      props: { run, folderId: 1 },
      global: { stubs },
    });
    mounted.push(wrapper);
    await flushPromises();
    expect(getTrainingJobLive).not.toHaveBeenCalled();
    expect(wrapper.find(".trl-name").text()).toBe("walter_v2");
    expect(wrapper.find(".trl-status b").text()).toBe("1,000 steps");
    expect(wrapper.find(".trl-notice").text()).toContain("Trained outside PixlStash.");
    expect(wrapper.find(".trl-notice").text()).toContain("no loss curve");
    expect(wrapper.findAll(".trl-cell").map((img) => img.attributes("src"))).toEqual([
      "/samples/1/walter_v2/a_0_0.jpg",
      "/samples/1/walter_v2/a_500_0.jpg",
    ]);
    // Nothing a job would have measured or done is drawn as if it had.
    expect(wrapper.find(".trl-statusbar").exists()).toBe(false);
    expect(wrapper.find(".trl-endph").exists()).toBe(false);
    expect(wrapper.find(".trl-setup").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Stop training");
    expect(wrapper.text()).not.toContain("Use this step");
  });
});

describe("leaving the run with Escape, as the back button's keycap says", () => {
  const press = (target = window) =>
    target.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("closes the run", async () => {
    const wrapper = await open(payload());
    press();
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it.each([
    ["a dialog or menu", "v-overlay v-overlay--active v-dialog"],
    ["the lightbox", "lightbox"],
    ["the review overlay", "rs-overlay"],
  ])("leaves the key to %s in front of the page", async (_name, className) => {
    const wrapper = await open(payload());
    const overlay = document.createElement("div");
    overlay.className = className;
    document.body.appendChild(overlay);
    press();
    expect(wrapper.emitted("close")).toBeUndefined();
  });

  it("leaves the key to a focused row in the Models rail", async () => {
    const wrapper = await open(payload());
    const rail = document.createElement("div");
    rail.className = "mrail";
    const row = document.createElement("button");
    rail.appendChild(row);
    document.body.appendChild(rail);
    press(row);
    expect(wrapper.emitted("close")).toBeUndefined();
  });

  it("leaves the key to the auto-hide sidebar it dismisses", async () => {
    const wrapper = await open(payload());
    const sidebar = useSidebarStore();
    sidebar.setSidebarPinned(false);
    expect(sidebar.sidebarVisible && sidebar.sidebarOverlay).toBe(true);
    press();
    expect(wrapper.emitted("close")).toBeUndefined();
  });

  it("leaves the key to a text field", async () => {
    const wrapper = await open(payload());
    const input = document.createElement("input");
    document.body.appendChild(input);
    press(input);
    expect(wrapper.emitted("close")).toBeUndefined();
  });

  it("says Setting up in the bar and names the step only in the corner", async () => {
    const wrapper = await open(
      payload({
        phase: "loading",
        step: 0,
        previews: null,
        phases: [{ phase: "loading", seconds: 42 }],
        rows: payload().rows.map((row) => ({ ...row, cells: cells(Array(7).fill("future")) })),
        coverable: [],
        cover_step: null,
      }),
    );
    expect(wrapper.find(".trl-status").text()).toContain("Setting up");
    expect(wrapper.find(".trl-status").text()).not.toContain("Loading");
    expect(wrapper.find(".trl-setup").text()).toBe("Loading the model");
    // Step 0 is drawn after set-up; later steps say when they are due, from
    // the status bar's 16-21 min spread over the 1,500 steps.
    const future = wrapper.findAll(".trl-cell--future");
    expect(future[0].text()).toBe("after set-up");
    expect(future[1].text()).toBe("");
    const subs = wrapper.findAll(".trl-stepsub").map((n) => n.text());
    expect(subs[1]).toBe("~3 min");
    expect(subs[6]).toBe("~19 min");
  });

  it("dates a future column from the steps still to train", async () => {
    // Step 1,000 of 1,500 with 16-21 min left: 1,250 is half way there.
    const wrapper = await open(payload());
    expect(wrapper.findAll(".trl-stepsub")[5].text()).toBe("~9 min");
  });

  it("gives a finished run no due times", async () => {
    const wrapper = await open(
      payload({ status: "completed", phase: null, completed_at: "2026-10-26T02:00:00+00:00" }),
    );
    expect(wrapper.findAll(".trl-stepsub").map((n) => n.text()).join(" ")).not.toContain("~");
  });

  it("remembers the tips folded or opened by hand", async () => {
    window.localStorage.removeItem("pixlstash.trainingRunTips");
    const first = await open(payload());
    // jsdom lays nothing out, so nothing says the tips do not fit.
    expect(first.find(".trl-tips-fold--shut").exists()).toBe(false);
    await first.find(".section-head__chevron").trigger("click");
    expect(first.find(".trl-tips-fold--shut").exists()).toBe(true);
    expect(first.find(".trl-tips").text()).toContain("4 tips on what to look for");
    first.unmount();
    mounted.length = 0;
    const second = await open(payload());
    expect(second.find(".trl-tips-fold--shut").exists()).toBe(true);
    await second.find(".section-head__chevron").trigger("click");
    expect(second.find(".trl-tips-fold--shut").exists()).toBe(false);
    expect(second.find("[data-testid='trl-tips']").text()).toContain("Pick the step");
    expect(window.localStorage.getItem("pixlstash.trainingRunTips")).toBe("open");
    window.localStorage.removeItem("pixlstash.trainingRunTips");
  });
});
