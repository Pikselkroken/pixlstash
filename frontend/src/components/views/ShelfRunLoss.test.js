import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

const getTrainingJob = vi.fn();
vi.mock("../../api/training", () => ({
  getTrainingJob: (...args) => getTrainingJob(...args),
}));

import ShelfRunLoss from "./ShelfRunLoss.vue";

beforeEach(() => {
  getTrainingJob.mockReset();
});

describe("ShelfRunLoss", () => {
  it("draws the loss curve of the run it is given", async () => {
    getTrainingJob.mockResolvedValue({
      id: 42,
      loss_curve: [
        { step: 1, loss: 0.5 },
        { step: 2, loss: 0.4 },
      ],
    });
    const wrapper = mount(ShelfRunLoss, { props: { runId: 42 } });
    await flushPromises();
    expect(getTrainingJob).toHaveBeenCalledWith(42);
    expect(wrapper.find(".lora-loss-figure").attributes("aria-label")).toContain(
      "from 0.500 at step 1 to 0.400 at step 2",
    );
  });

  it("draws nothing for a run this library does not hold", async () => {
    getTrainingJob.mockRejectedValue(
      Object.assign(new Error("Training job not found"), {
        response: { status: 404 },
      }),
    );
    const wrapper = mount(ShelfRunLoss, { props: { runId: 42 } });
    await flushPromises();
    expect(wrapper.find(".lora-loss-figure").exists()).toBe(false);
  });

  it("asks for nothing when there is no run", async () => {
    const wrapper = mount(ShelfRunLoss, { props: { runId: null } });
    await flushPromises();
    expect(getTrainingJob).not.toHaveBeenCalled();
    expect(wrapper.find(".lora-loss-figure").exists()).toBe(false);
  });
});
