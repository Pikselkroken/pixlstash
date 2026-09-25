// "Make more like these…" over a mixed selection (v1.12 F5).
//
// The thing this file guards is the DISABLED state. A run that cannot happen
// stays on screen with its reason, and a missing model stops the whole batch
// rather than just its own group — which is the server's own rule
// (`workflow_run_service.blocks_batch`: installing a file is a trip away from
// the keyboard, so queueing the rest would leave the owner repeating the
// gesture to catch what was skipped). Filtering the group out instead would
// make the count add up and say nothing about why.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listWorkflowCards = vi.fn();
const preflightWorkflowRun = vi.fn();
const runWorkflowCard = vi.fn();

vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
  runWorkflowCard: (...args) => runWorkflowCard(...args),
}));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/api/v1/pictures/thumbnails/${id}.webp`,
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import MakeMoreDialog from "./MakeMoreDialog.vue";

const GOOD = "a".repeat(64);
const BAD = "b".repeat(64);

const AppDialogStub = {
  name: "AppDialog",
  template: "<div><slot /><footer><slot name='footer' /></footer></div>",
};

const globalOpts = {
  global: {
    stubs: {
      AppDialog: AppDialogStub,
      AppSelect: true,
      AppInput: true,
      AppButton: { template: "<button><slot /></button>" },
      "v-icon": true,
    },
  },
};

async function mountMakeMore(pictureIds = [1, 2, 3]) {
  const wrapper = mount(MakeMoreDialog, {
    props: { open: true, source: { pictureIds }, context: {} },
    ...globalOpts,
  });
  await flushPromises();
  return wrapper;
}

function makeButton(wrapper) {
  return wrapper
    .findAll("button")
    .find((b) => b.text().startsWith("Make"));
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  listWorkflowCards.mockResolvedValue({
    cards: [
      { key: GOOD, name: "Cinematic portrait" },
      { key: BAD, name: "Z-Image turbo" },
    ],
  });
  runWorkflowCard.mockResolvedValue({ status: "success", prompts: [{ prompt_id: "p" }] });
});

describe("a selection that spans several recipes", () => {
  beforeEach(() => {
    preflightWorkflowRun.mockResolvedValue({
      ok: false,
      runs: 0,
      groups: [
        { workflow_key: GOOD, picture_ids: [1, 2], runs: 1, reasons: [] },
        {
          workflow_key: BAD,
          picture_ids: [3],
          runs: 0,
          reasons: [
            {
              code: "missing_models",
              models: [
                {
                  file: "z_image_turbo_bf16.safetensors",
                  folder: "diffusion_models",
                },
              ],
            },
          ],
        },
      ],
    });
  });

  it("says how many pictures use how many recipes", async () => {
    const wrapper = await mountMakeMore();
    expect(wrapper.text()).toContain("3 pictures use");
    expect(wrapper.text()).toContain("2 different recipes");
  });

  it("keeps the group that cannot run on screen, faded, with its count", async () => {
    const wrapper = await mountMakeMore();
    const rows = wrapper.findAll(".mmd-row");
    expect(rows).toHaveLength(2);
    const blocked = rows[1];
    expect(blocked.classes()).toContain("mmd-row--off");
    expect(blocked.text()).toContain("Z-Image turbo");
    expect(blocked.text()).toContain("1 picture");
  });

  it("offers no action at all when there is nothing to press", async () => {
    // A missing model has no fix this popup can carry out, and a general
    // "learn more" link cannot say more about THIS run than the sentence
    // already does - it just has to be tried before it can be dismissed.
    const wrapper = await mountMakeMore();
    const notice = wrapper.findComponent({ name: "RunReasonNotice" });
    expect(notice.find(".rrn-acts").exists()).toBe(false);
    expect(notice.find("a").exists()).toBe(false);
  });

  it("names the missing file and the folder it belongs in", async () => {
    const wrapper = await mountMakeMore();
    const notice = wrapper.findComponent({ name: "RunReasonNotice" });
    expect(notice.text()).toContain("z_image_turbo_bf16.safetensors");
    expect(notice.text()).toContain("models/diffusion_models");
    expect(notice.text()).toContain("Z-Image turbo can't run.");
  });

  it("blocks the WHOLE run on the missing model, not just its own group", async () => {
    const wrapper = await mountMakeMore();
    // The two runnable pictures are NOT offered on their own: that is the
    // server's rule, and a client that queued them would leave the owner
    // repeating the gesture after installing the file.
    expect(wrapper.vm.totalRuns).toBe(0);
    // `aria-disabled`, not the native attribute: a natively-disabled button is
    // out of the tab order, so the reason below could never be reached.
    const button = makeButton(wrapper);
    expect(button.attributes("aria-disabled")).toBe("true");
    const describedBy = button.attributes("aria-describedby");
    expect(wrapper.find(`#${describedBy}`).text()).toBe(
      "Missing models block the whole run.",
    );
  });

  it("does not send a run while it is blocked", async () => {
    const wrapper = await mountMakeMore();
    await wrapper.vm.submit();
    expect(runWorkflowCard).not.toHaveBeenCalled();
  });
});

describe("a selection every picture of which can run", () => {
  beforeEach(() => {
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 3,
      groups: [
        { workflow_key: GOOD, picture_ids: [1, 2], runs: 1, reasons: [] },
        { workflow_key: BAD, picture_ids: [3], runs: 1, reasons: [] },
      ],
    });
  });

  it("counts one run per RECIPE per count, not one per picture", async () => {
    // `_submit_every` loops `for _ in range(body.count)` inside its loop over
    // the GROUPS: one graph per card, submitted `count` times. Two recipes at
    // a count of four is eight runs, and the three pictures only chose which
    // two cards run. A button reading "Make 12" would promise four runs that
    // never happen.
    const wrapper = await mountMakeMore();
    wrapper.vm.count = 4;
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.totalRuns).toBe(8);
    expect(makeButton(wrapper).text()).toBe("Make 8");

    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0]).toMatchObject({
      picture_ids: [1, 2, 3],
      count: 4,
      seed_mode: "new",
    });
    expect(wrapper.emitted("run")[0][0].pictureIds).toEqual([1, 2, 3]);
  });

  it("counts once per PICTURE for a card its pictures are fed into (#1457)", async () => {
    // The server feeds each picture into a card with one open picture input
    // and repeats the run per picture, and says so with `fill: "selection"`.
    // Read as "per recipe" that card would promise 4 runs and start 8.
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 3,
      groups: [
        {
          workflow_key: GOOD,
          picture_ids: [1, 2],
          runs: 2,
          reasons: [],
          picture_inputs: [{ slot_label: "s", input_name: "image", fill: "selection" }],
        },
        { workflow_key: BAD, picture_ids: [3], runs: 1, reasons: [] },
      ],
    });
    const wrapper = await mountMakeMore();
    wrapper.vm.count = 4;
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.totalRuns).toBe(12);
    expect(makeButton(wrapper).text()).toBe("Make 12");
    expect(wrapper.text()).toContain("Its own recipe, run on each of these pictures");
  });

  it("refuses a request over the server's 200-run ceiling, saying the number", async () => {
    // `_plan` caps the TOTAL, not the count, and answers 400. Pressing Run to
    // find that out is the round trip this avoids.
    const wrapper = await mountMakeMore();
    wrapper.vm.count = 150;
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.totalRuns).toBe(300);
    expect(wrapper.vm.blocker).toBe(
      "That is 300 runs; at most 200 start at once.",
    );
    await wrapper.vm.submit();
    expect(runWorkflowCard).not.toHaveBeenCalled();
  });

  it("spends the caller's pre-flight instead of asking the same question twice", async () => {
    // Deciding which popup to open already cost one pre-flight, and each one
    // costs the server a ComfyUI /object_info read.
    const handed = {
      ok: true,
      runs: 2,
      groups: [
        { workflow_key: GOOD, picture_ids: [1, 2], runs: 1, reasons: [] },
        { workflow_key: BAD, picture_ids: [3], runs: 1, reasons: [] },
      ],
    };
    const wrapper = mount(MakeMoreDialog, {
      props: {
        open: true,
        source: { pictureIds: [1, 2, 3], preflight: handed },
        context: {},
      },
      ...globalOpts,
    });
    await flushPromises();

    expect(preflightWorkflowRun).not.toHaveBeenCalled();
    expect(wrapper.findAll(".mmd-row")).toHaveLength(2);

    // A Retry is a deliberate re-ask, so the handed answer is spent once only.
    await wrapper.vm.load();
    await flushPromises();
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
  });

  it("keeps the count and the seed across a Retry", async () => {
    // Retry shares `load()` with the open. Resetting there turned "Make 16"
    // into "Make 2" the moment somebody re-asked an unreachable ComfyUI, with
    // nothing said - the button quietly promising something else.
    const wrapper = await mountMakeMore();
    wrapper.vm.count = 8;
    wrapper.vm.seedMode = "keep";
    await wrapper.vm.$nextTick();
    expect(makeButton(wrapper).text()).toBe("Make 16");

    await wrapper.vm.load();
    await flushPromises();

    expect(wrapper.vm.count).toBe(8);
    expect(wrapper.vm.seedMode).toBe("keep");
    expect(makeButton(wrapper).text()).toBe("Make 16");
  });

  it("reads \"Make\" rather than \"Make 0\" when nothing can run", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ok: false,
      runs: 0,
      groups: [
        {
          workflow_key: BAD,
          picture_ids: [3],
          runs: 0,
          reasons: [{ code: "missing_models", models: [{ file: "x", folder: "y" }] }],
        },
      ],
    });
    const wrapper = await mountMakeMore();
    expect(makeButton(wrapper).text()).toBe("Make");
  });

  it("still makes every run when a LoRA is only bypassed (#1463)", async () => {
    // The reported symptom: one absent LoRA on one card refused a selection
    // that was otherwise entirely runnable. It is not a reason, so it neither
    // zeroes the total nor takes its own card out of it.
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 2,
      groups: [
        { workflow_key: GOOD, picture_ids: [1, 2], runs: 1, reasons: [] },
        {
          workflow_key: BAD,
          picture_ids: [3],
          runs: 1,
          reasons: [],
          bypassed_loras: [{ file: "character.safetensors", folder: "loras" }],
        },
      ],
    });

    const wrapper = await mountMakeMore();

    expect(wrapper.vm.blocker).toBe("");
    expect(wrapper.vm.runnable).toHaveLength(2);
    expect(makeButton(wrapper).text()).toBe("Make 2");
    // And it is said, on the card it is about, before the button is pressed.
    expect(wrapper.vm.blockedReasons.map((entry) => entry.reason.code)).toEqual([
      "loras_bypassed",
    ]);
    expect(wrapper.vm.blockedReasons[0].subject).toBe("Z-Image turbo");
  });

  it("still makes every run when a seed node is only replaced (#1463)", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 2,
      groups: [
        { workflow_key: GOOD, picture_ids: [1, 2], runs: 1, reasons: [] },
        {
          workflow_key: BAD,
          picture_ids: [3],
          runs: 1,
          reasons: [],
          replaced_nodes: [
            { node_id: "9", class_type: "Seed (rgthree)", replacement: "seed" },
          ],
        },
      ],
    });

    const wrapper = await mountMakeMore();

    expect(wrapper.vm.blocker).toBe("");
    expect(makeButton(wrapper).text()).toBe("Make 2");
    expect(wrapper.vm.blockedReasons.map((entry) => entry.reason.code)).toEqual([
      "nodes_replaced",
    ]);
  });

  it("starts each open at a count of 1, never the last selection's", async () => {
    const wrapper = await mountMakeMore();
    wrapper.vm.count = 20;
    await wrapper.vm.$nextTick();

    await wrapper.setProps({ source: { pictureIds: [7, 8] } });
    await flushPromises();

    expect(wrapper.vm.count).toBe(1);
    expect(wrapper.vm.seedMode).toBe("new");
  });
});
