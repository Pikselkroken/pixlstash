// More angles… (#2819): one shots video from the chosen picture, its frames
// taken out by the server, each restored from its frame with the picture as
// the second reference, checked, and the kept views put in the location's set
// with the video, the frames sent to the Scrapheap.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  buildLocationShotsPrompt: vi.fn(),
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
}));
vi.mock("../../api/characterSheets", () => ({
  buildLocationShotsPrompt: api.buildLocationShotsPrompt,
  takeLocationShots: api.takeLocationShots,
  checkLocationShots: api.checkLocationShots,
}));
vi.mock("../../api/pictureSets", () => ({ bulkAddPicturesToSet: api.bulkAddPicturesToSet }));
vi.mock("../../api/pictures", () => ({
  deletePictures: api.deletePictures,
  restoreScrapheap: api.restoreScrapheap,
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));
vi.mock("../../api/workflows", () => ({
  getRunOutcome: api.getRunOutcome,
  listWorkflowCards: api.listWorkflowCards,
  preflightWorkflowRun: api.preflightWorkflowRun,
  runWorkflowCard: api.runWorkflowCard,
  stopRun: api.stopRun,
}));

import LocationAnglesDialog from "./LocationAnglesDialog.vue";

const LOCATION = {
  id: 30,
  name: "The pub",
  description: "a pub",
  picture_ids: [31, 32, 33, 34],
};

const OUTPUTS = { "run-video": [500], "run-r1": [601], "run-r2": [602], "run-r3": [603] };

beforeEach(() => {
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

    await wrapper.find('[data-testid="la-use"]').trigger("click");
    await flushPromises();
    expect(api.restoreScrapheap).toHaveBeenCalledWith([601, 603]);
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(30, [500, 601, 603]);
    expect(api.deletePictures).toHaveBeenCalledWith([701, 702, 703]);
    expect(wrapper.emitted("done")[0]).toEqual([2]);
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

  it("stops following its runs when closed mid-draw", async () => {
    api.getRunOutcome.mockResolvedValue({ status: "running", picture_ids: [] });
    vi.useFakeTimers();
    const wrapper = mountDialog();
    await wrapper.find('[data-testid="la-draw"]').trigger("click");
    await vi.advanceTimersByTimeAsync(10);
    await wrapper.findAll("button").find((b) => b.text() === "Cancel").trigger("click");
    // The video then finishes: a draw still following it would take frames.
    api.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [500] });
    await vi.advanceTimersByTimeAsync(10000);
    vi.useRealTimers();
    expect(api.stopRun).toHaveBeenCalledWith("run-video");
    expect(api.takeLocationShots).not.toHaveBeenCalled();
  });
});
