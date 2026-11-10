// Train longer's popover: which kept step, how many more, and the one call.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getTrainLongerOptions = vi.fn();
const trainJobLonger = vi.fn();
vi.mock("../../api/training", () => ({
  getTrainLongerOptions: (...args) => getTrainLongerOptions(...args),
  trainJobLonger: (...args) => trainJobLonger(...args),
}));

import TrainLongerPopover from "./TrainLongerPopover.vue";

const FINISHED = {
  status: "completed",
  total_steps: 3000,
  save_every: 250,
  seconds_per_step: 2.1,
  reason: null,
  steps: [
    { step: 3000, final: true, keeps_optimizer: true },
    { step: 2750, final: false, keeps_optimizer: false },
    { step: 2500, final: false, keeps_optimizer: false },
  ],
};

async function open(answer, props = {}) {
  getTrainLongerOptions.mockResolvedValue(answer);
  const wrapper = mount(TrainLongerPopover, {
    props: { jobId: 7, ...props },
    global: { stubs: { "v-icon": true } },
  });
  await flushPromises();
  return wrapper;
}

const slider = (w) => w.find('[data-testid="tlp-slider"]');
const field = (w) => w.find('[data-testid="tlp-number"] input');
const ends = (w) => w.find('[data-testid="tlp-ends"]').text();

describe("TrainLongerPopover", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    getTrainLongerOptions.mockReset();
    trainJobLonger.mockReset();
  });

  it("preselects the final, which alone keeps the optimizer, and adds 1,000", async () => {
    const w = await open(FINISHED);
    const rows = w.findAll('[role="radio"]');
    expect(rows.map((r) => r.text())).toEqual([
      "Step 3,000 · finalkeeps the optimizer",
      "Step 2,750",
      "Step 2,500",
    ]);
    expect(rows[0].attributes("aria-checked")).toBe("true");
    expect(field(w).element.value).toBe("1000");
    expect(ends(w)).toBe("Ends at step 4,000 · about 35 min");
  });

  it("defaults a stopped run to what is left of its target", async () => {
    const w = await open({
      ...FINISHED,
      status: "stopped",
      total_steps: 2000,
      steps: [
        { step: 1250, final: false, keeps_optimizer: true },
        { step: 1000, final: false, keeps_optimizer: false },
      ],
    });
    expect(w.findAll('[role="radio"]')[0].text()).toContain(
      "Step 1,250 · where it stopped",
    );
    expect(field(w).element.value).toBe("750");
    expect(ends(w)).toContain("Ends at step 2,000");
  });

  it("binds the slider and the field to one value, past the slider's end too", async () => {
    const w = await open(FINISHED);
    expect(slider(w).attributes("step")).toBe("250");
    await slider(w).setValue("2500");
    expect(field(w).element.value).toBe("2500");

    await field(w).setValue("5000");
    // The thumb pins at the end; the number is kept.
    expect(slider(w).element.value).toBe("3000");
    expect(ends(w)).toContain("Ends at step 8,000");
  });

  it("starts the run from the chosen step with the steps given", async () => {
    trainJobLonger.mockResolvedValue({ job_id: 7, status: "queued" });
    const w = await open(FINISHED);
    await w.findAll('[role="radio"]')[2].trigger("click");
    await field(w).setValue("600");
    await w.find('[data-testid="tlp-start"]').trigger("click");
    await flushPromises();
    expect(trainJobLonger).toHaveBeenCalledWith(7, 2500, 600);
    expect(w.emitted("started")).toHaveLength(1);
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("says why a run cannot be trained longer and offers no Start", async () => {
    const w = await open({ ...FINISHED, steps: [], reason: "It kept no checkpoint." });
    expect(w.find('[data-testid="tlp-reason"]').text()).toBe("It kept no checkpoint.");
    expect(w.find('[data-testid="tlp-start"]').attributes("disabled")).toBeDefined();
    await w.find('[data-testid="tlp-start"]').trigger("click");
    expect(trainJobLonger).not.toHaveBeenCalled();
  });
});
