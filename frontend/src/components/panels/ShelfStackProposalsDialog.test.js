// The stack dry run.
//
// The assertion worth having is that opening it groups nothing. "Detection
// proposes, it never applies" is a promise made in three places in this
// codebase, and this dialog is the surface where breaking it would rearrange
// somebody's shelf without a press.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listStackProposals = vi.fn();
const createStack = vi.fn();
vi.mock("../../api/modelStacks", () => ({
  listStackProposals: (...args) => listStackProposals(...args),
  createStack: (...args) => createStack(...args),
}));

// The shelf refresh after an apply. Stubbed to its one method so this suite
// does not pull the whole store's network surface in behind it.
const fetchRows = vi.fn();
vi.mock("../../stores/useModelShelfStore", () => ({
  useModelShelfStore: () => ({ fetchRows }),
}));

import ShelfStackProposalsDialog from "./ShelfStackProposalsDialog.vue";
import { useNoticeStore } from "../../stores/useNoticeStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      AppDialog: {
        props: ["open"],
        template: "<div v-if='open'><slot /><slot name='footer' /></div>",
      },
      AppButton: {
        template: "<button :disabled='disabled'><slot /></button>",
        props: ["disabled", "loading", "variant", "keyHint"],
      },
    },
  },
};

function proposal(overrides = {}) {
  return {
    tier: "step_group",
    key: "1:jimmyvehicle",
    name: "JimmyVehicle",
    folder_id: 1,
    total_size: 3000,
    members: [
      { model_id: 1, filename: "JimmyVehicle.safetensors", step: null },
      {
        model_id: 2,
        filename: "JimmyVehicle_000001000.safetensors",
        step: 1000,
      },
      {
        model_id: 3,
        filename: "JimmyVehicle_000000500.safetensors",
        step: 500,
      },
    ],
    ...overrides,
  };
}

async function openWith(proposals) {
  listStackProposals.mockResolvedValue(proposals);
  const wrapper = mount(ShelfStackProposalsDialog, {
    ...globalOpts,
    props: { open: true },
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  listStackProposals.mockReset();
  createStack.mockReset();
  fetchRows.mockReset().mockResolvedValue(undefined);
});

describe("the dry run", () => {
  it("lists the runs without grouping any of them", async () => {
    const wrapper = await openWith([proposal()]);
    expect(wrapper.findAll(".ssp-group")).toHaveLength(1);
    expect(createStack).not.toHaveBeenCalled();
  });

  it("says which file will represent the run", async () => {
    // The one decision in a group a reader might disagree with, and it
    // is not readable from a list of steps.
    const wrapper = await openWith([proposal()]);
    expect(wrapper.find(".ssp-cover").text()).toContain("the final file");
  });

  it("names the version as the cover when the group spans versions", async () => {
    // The whole decision in a `version_group`: the reader is agreeing that v2
    // is what "Foxglove" now means on the shelf. No member here carries a step,
    // so without the version the line would say "the final file" about three
    // files that are all final.
    const wrapper = await openWith([
      proposal({
        tier: "version_group",
        key: "1:foxglove",
        name: "Foxglove",
        members: [
          {
            model_id: 1,
            filename: "Foxglove_v2.safetensors",
            step: null,
            version: "v2",
          },
          {
            model_id: 2,
            filename: "Foxglove.safetensors",
            step: null,
            version: null,
          },
        ],
      }),
    ]);
    // The exact string, not `toContain("v2")`: the documented decision is that
    // the version LEADS, and "the final file, v2" would satisfy a containment
    // check while saying the opposite.
    expect(wrapper.find(".ssp-cover").text()).toBe(
      "Shown as v2, the final file",
    );
  });

  it("names the highest step as the cover when there is no final", async () => {
    const wrapper = await openWith([
      proposal({
        members: [
          { model_id: 2, filename: "x_000002750.safetensors", step: 2750 },
          { model_id: 3, filename: "x_000000500.safetensors", step: 500 },
        ],
      }),
    ]);
    expect(wrapper.find(".ssp-cover").text()).toContain("step 2750");
  });

  it("explains itself when there is nothing to group", async () => {
    const wrapper = await openWith([]);
    expect(wrapper.text()).toContain("differ by a training step");
    expect(wrapper.findAll(".ssp-group")).toHaveLength(0);
  });

  it("ticks a step group, because that much is a batch confirmation", async () => {
    const wrapper = await openWith([
      proposal(),
      proposal({ key: "1:foxglove", name: "Foxglove" }),
    ]);
    const boxes = wrapper.findAll(".ssp-head input");
    expect(boxes).toHaveLength(2);
    expect(boxes.every((b) => b.element.checked)).toBe(true);
  });

  it("leaves a version group UNTICKED, because it merges trainings", async () => {
    // The destructive default this dialog must not have. A version group fuses
    // runs the owner may have kept apart on purpose — so one press must not be
    // able to do it by omission, even now that Ungroup can take it back.
    const wrapper = await openWith([
      proposal(),
      proposal({
        tier: "version_group",
        key: "1:foxglove",
        name: "Foxglove",
      }),
    ]);
    const boxes = wrapper.findAll(".ssp-head input");
    expect(boxes.map((b) => b.element.checked)).toEqual([true, false]);
  });

  it("says on the row itself which group would merge versions", async () => {
    // Unticked is not enough on its own: a reader scanning for what to tick
    // needs the risky rows named, not inferred from an empty checkbox.
    const wrapper = await openWith([
      proposal({ tier: "version_group", key: "1:foxglove", name: "Foxglove" }),
    ]);
    expect(wrapper.find(".ssp-tier").text()).toBe("merges versions");
  });

  it("marks no tier chip on an ordinary step group", async () => {
    // The positive control: the chip is a warning, so drawing it on every row
    // would make it mean nothing.
    const wrapper = await openWith([proposal()]);
    expect(wrapper.find(".ssp-tier").exists()).toBe(false);
  });
});

describe("applying", () => {
  it("sends one call per run, with the run's own members", async () => {
    const wrapper = await openWith([proposal()]);
    createStack.mockResolvedValue({ stack_id: 1, member_count: 3 });

    await wrapper.findAll("button").at(-1).trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(createStack).toHaveBeenCalledTimes(1);
    expect(createStack).toHaveBeenCalledWith([1, 2, 3], "JimmyVehicle");
  });

  it("keeps the groups that landed when one is refused", async () => {
    // A group whose rows were stacked between the dry run and the press comes
    // back 409. One stale group must not discard the others.
    const wrapper = await openWith([
      proposal(),
      proposal({ key: "1:foxglove", name: "Foxglove" }),
    ]);
    createStack
      .mockResolvedValueOnce({ stack_id: 1, member_count: 3 })
      .mockRejectedValueOnce(new Error("already stacked"));

    await wrapper.findAll("button").at(-1).trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("success");
    expect(notice.text).toContain("Grouped 1 stack.");
    expect(notice.text).toContain("1 stack could not be grouped");
    expect(fetchRows).toHaveBeenCalled();
  });

  it("cannot be submitted with every group unticked", async () => {
    const wrapper = await openWith([proposal()]);
    await wrapper.find(".ssp-head input").setValue(false);
    expect(
      wrapper.findAll("button").at(-1).attributes("disabled"),
    ).toBeDefined();
  });
});
