// Replace missing models: one choice per missing file, written to every
// workflow that loads it, manual workflows left out, refusals listed.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const readModelSwap = vi.fn();
const setWorkflowModelFix = vi.fn();
vi.mock("../../api/workflows", () => ({
  readModelSwap: (...args) => readModelSwap(...args),
  setWorkflowModelFix: (...args) => setWorkflowModelFix(...args),
  getWorkflowCard: vi.fn(async (id) => ({ card: { id, name: `Name of ${id}` } })),
}));
const fetchWorkflowSets = vi.fn(async () => ({ combinations: [], no_set: [] }));
vi.mock("../../api/modelShelf", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import ReplaceMissingDialog from "./ReplaceMissingDialog.vue";
import { notifySessionReset } from "../../utils/apiClient";

const HEADS = [
  {
    name: "gone.sft",
    names: ["gone.sft"],
    workflowIds: ["auto:a", "auto:b", "manual:c"],
    workflowsByName: { "gone.sft": ["auto:a", "auto:b", "manual:c"] },
  },
  {
    name: "old.sft",
    names: ["old.sft"],
    workflowIds: ["auto:d"],
    workflowsByName: { "old.sft": ["auto:d"] },
  },
];

async function mountDialog() {
  const wrapper = mount(ReplaceMissingDialog, {
    props: { open: true, heads: HEADS },
    global: { stubs: { teleport: true } },
  });
  await flushPromises();
  return wrapper;
}

const applyButton = (wrapper) => wrapper.find('[data-testid="rmd-apply"]');

describe("ReplaceMissingDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    readModelSwap.mockImplementation(async (id, { replacing }) =>
      replacing === "gone.sft"
        ? { replacements: [{ id: 9, filename: "new.sft", display_name: "New" }] }
        : { replacements: [], replacements_reason: "none_go_with_it" },
    );
    setWorkflowModelFix.mockResolvedValue({});
  });

  it("asks the first fixable workflow, and says why a file has no candidate", async () => {
    const wrapper = await mountDialog();
    expect(readModelSwap).toHaveBeenCalledWith("auto:a", {
      replacing: "gone.sft",
      slotKind: "checkpoint",
    });
    expect(wrapper.text()).toContain("Name of auto:a · Name of auto:b");
    expect(wrapper.text()).toContain("1 manual workflow loads it too");
    expect(wrapper.text()).toContain("known to work with this checkpoint");
    expect(applyButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.find('[data-testid="rmd-unmatched"]').exists()).toBe(false);
  });

  it("says so when the offer is every checkpoint, none known to match", async () => {
    readModelSwap.mockResolvedValue({
      replacements: [{ id: 9, filename: "new.sft" }],
      replacements_narrowed: false,
    });
    const wrapper = await mountDialog();
    expect(wrapper.find('[data-testid="rmd-unmatched"]').text()).toContain(
      "is known to match it",
    );
  });

  it("writes the choice to every workflow loading the file, never a manual one", async () => {
    const wrapper = await mountDialog();
    await wrapper.find("select").setValue("new.sft");
    expect(applyButton(wrapper).text()).toContain("Replace in 2 workflows");
    await applyButton(wrapper).trigger("click");
    await flushPromises();
    expect(setWorkflowModelFix.mock.calls).toEqual([
      ["auto:a", { was: "gone.sft", now: "new.sft", slot_kind: "checkpoint" }],
      ["auto:b", { was: "gone.sft", now: "new.sft", slot_kind: "checkpoint" }],
    ]);
    expect(fetchWorkflowSets).toHaveBeenCalled();
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("retries only the workflows that refused", async () => {
    setWorkflowModelFix
      .mockRejectedValueOnce({ response: { data: { detail: "Busy." } } })
      .mockResolvedValue({});
    const wrapper = await mountDialog();
    await wrapper.find("select").setValue("new.sft");
    await applyButton(wrapper).trigger("click");
    await flushPromises();
    expect(applyButton(wrapper).text()).not.toContain("2 workflows");
    await applyButton(wrapper).trigger("click");
    await flushPromises();
    expect(setWorkflowModelFix.mock.calls.map(([id]) => id)).toEqual([
      "auto:a",
      "auto:b",
      "auto:a",
    ]);
  });

  it("writes a changed choice again to workflows that took the old one", async () => {
    readModelSwap.mockResolvedValue({
      replacements: [
        { id: 9, filename: "new.sft", display_name: "New" },
        { id: 8, filename: "other.sft", display_name: "Other" },
      ],
    });
    setWorkflowModelFix
      .mockRejectedValueOnce({ response: { data: { detail: "Busy." } } })
      .mockResolvedValue({});
    const wrapper = await mountDialog();
    await wrapper.find("select").setValue("new.sft");
    await applyButton(wrapper).trigger("click");
    await flushPromises();
    await wrapper.find("select").setValue("other.sft");
    await applyButton(wrapper).trigger("click");
    await flushPromises();
    expect(
      setWorkflowModelFix.mock.calls.slice(2).map(([id, fix]) => [id, fix.now]),
    ).toEqual([
      ["auto:a", "other.sft"],
      ["auto:b", "other.sft"],
    ]);
  });

  it("stops writing and closes when the credential changes mid-run", async () => {
    setWorkflowModelFix.mockImplementationOnce(async () => {
      notifySessionReset();
      return {};
    });
    const wrapper = await mountDialog();
    await wrapper.find("select").setValue("new.sft");
    await applyButton(wrapper).trigger("click");
    await flushPromises();
    expect(setWorkflowModelFix).toHaveBeenCalledTimes(1);
    expect(fetchWorkflowSets).not.toHaveBeenCalled();
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("says nothing about workflows it would write when only manual ones load it", async () => {
    const wrapper = mount(ReplaceMissingDialog, {
      props: {
        open: true,
        heads: [
          {
            name: "solo.sft",
            names: ["solo.sft"],
            workflowIds: ["manual:x"],
            workflowsByName: { "solo.sft": ["manual:x"] },
          },
        ],
      },
      global: { stubs: { teleport: true } },
    });
    await flushPromises();
    expect(wrapper.text()).not.toContain("Loaded by");
    expect(wrapper.text()).toContain("1 manual workflow loads it; clone it");
  });

  it("lists a refusal by workflow, keeps going, and stays open", async () => {
    setWorkflowModelFix
      .mockRejectedValueOnce({ response: { data: { detail: "Not loaded there." } } })
      .mockResolvedValueOnce({});
    const wrapper = await mountDialog();
    await wrapper.find("select").setValue("new.sft");
    await applyButton(wrapper).trigger("click");
    await flushPromises();
    expect(setWorkflowModelFix).toHaveBeenCalledTimes(2);
    expect(wrapper.find('[role="alert"]').text()).toContain("Name of auto:a");
    expect(wrapper.emitted("close")).toBeUndefined();
  });
});
