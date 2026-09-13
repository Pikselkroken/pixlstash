// The workflow inspector's Pictures tab, which has three sentences and has now
// shipped the wrong one twice.
//
// The tab answers "what did this workflow make", and the three answers are not
// interchangeable: the workflow outlived its pictures, we have not looked yet,
// or we looked and could not read them. Saying "nothing this workflow made is
// still in the library" when a request merely failed is the one thing this
// panel must not do — and the first fix for that traded it for "Reading its
// pictures…" for ever, which made the sentence written for the failure
// unreachable. Both bugs lived in four lines that had no test.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listWorkflows = vi.fn();
const listWorkflowVariants = vi.fn();
const listWorkflowPictures = vi.fn();

vi.mock("../../api/workflows", () => ({
  listWorkflows: (...args) => listWorkflows(...args),
  listWorkflowVariants: (...args) => listWorkflowVariants(...args),
  listWorkflowPictures: (...args) => listWorkflowPictures(...args),
  getWorkflowGraph: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

import WorkflowInspector from "./WorkflowInspector.vue";
import { useWorkflowShelfStore } from "../../stores/useWorkflowShelfStore";
import { useSidebarStore } from "../../stores/useSidebarStore";

const HASH = "a".repeat(64);

const globalOpts = {
  global: { stubs: { "v-icon": true } },
};

function workflow(overrides = {}) {
  return {
    topology_hash: HASH,
    hash_version: "v1",
    node_count: 47,
    first_seen_at: "2026-08-01T00:00:00Z",
    variants: 1,
    pictures: 1075,
    last_used: "2026-08-20T00:00:00Z",
    assets: [{ widget: "ckpt_name", name: "realvisxlv40.safetensors" }],
    adapter_slots: 0,
    ...overrides,
  };
}

/** Mount the rail with one workflow selected and the Pictures tab open. */
async function mountOnPictures(row = workflow()) {
  const store = useWorkflowShelfStore();
  store.rows = [row];
  store.select(row.topology_hash);
  const wrapper = mount(WorkflowInspector, globalOpts);
  await flush(wrapper);
  const pictures = wrapper
    .findAll("button.wfins-tab-btn")
    .find((b) => b.text().includes("Pictures"));
  if (!pictures.attributes("disabled")) {
    await pictures.trigger("click");
    await wrapper.vm.$nextTick();
  }
  return { wrapper, store, pictures };
}

async function flush(wrapper) {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
}

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  useSidebarStore().statsOpen = true;
  listWorkflows.mockReset();
  listWorkflowVariants.mockReset().mockResolvedValue([]);
  listWorkflowPictures.mockReset().mockResolvedValue([]);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("the Pictures tab's three answers", () => {
  it("draws the tiles when they arrive", async () => {
    listWorkflowPictures.mockResolvedValue([11, 12, 13]);
    const { wrapper } = await mountOnPictures();
    expect(wrapper.findAll("button.wfins-tile")).toHaveLength(3);
  });

  it("says the workflow outlived its pictures when the ids come back empty", async () => {
    // Reachable only against a POSITIVE count, and that is not a contrivance:
    // the tab is disabled when the list says zero, so the sentence renders
    // exactly when the tile query disagrees with the count beside it — a
    // picture deleted between the two reads. The tile query is the later of the
    // two and is therefore the one to believe.
    listWorkflowPictures.mockResolvedValue([]);
    const { wrapper } = await mountOnPictures();
    expect(textOf(wrapper)).toContain(
      "Nothing this workflow made is still in the library",
    );
    expect(textOf(wrapper)).not.toContain("Could not read");
  });

  it("does NOT say that when the request failed — and does not hang on 'reading' either", async () => {
    // The two bugs this file exists for, in one assertion each way round.
    listWorkflowPictures.mockRejectedValue(new Error("network"));
    const { wrapper } = await mountOnPictures();

    const text = textOf(wrapper);
    expect(text).toContain("Could not read its pictures just now");
    expect(text).not.toContain("Nothing this workflow made is still");
    expect(text).not.toContain("Reading its pictures");
    // It still says what the list knows, which the failed request does not
    // change.
    expect(text).toContain("1 075");
  });

  it("offers a way out of the failure, and the retry can succeed", async () => {
    listWorkflowPictures.mockRejectedValueOnce(new Error("network"));
    const { wrapper } = await mountOnPictures();

    const retry = wrapper
      .findAll("button.wfins-action")
      .find((b) => b.text().includes("Try again"));
    expect(retry).toBeTruthy();

    listWorkflowPictures.mockResolvedValue([21, 22]);
    await retry.trigger("click");
    await flush(wrapper);

    expect(wrapper.findAll("button.wfins-tile")).toHaveLength(2);
    expect(textOf(wrapper)).not.toContain("Could not read");
  });
});

describe("the tab strip", () => {
  it("dims Pictures rather than removing it when nothing is left", async () => {
    // The panel keeps its shape: the tab is the way out to the pictures, and a
    // workflow that outlived them must not change what the rail looks like.
    listWorkflowPictures.mockResolvedValue([]);
    const { pictures } = await mountOnPictures(workflow({ pictures: 0 }));
    expect(pictures.exists()).toBe(true);
    expect(pictures.attributes("disabled")).toBeDefined();
  });

  it("names the subject, never the view", async () => {
    listWorkflowPictures.mockResolvedValue([]);
    const { wrapper } = await mountOnPictures();
    const tabs = wrapper.findAll("button.wfins-tab-btn").map((b) => b.text());
    expect(tabs[0]).toContain("Workflow");
    expect(tabs[1]).toContain("Pictures");
  });
});
