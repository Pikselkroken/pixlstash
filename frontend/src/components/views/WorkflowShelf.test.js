// The Workflows view's reading surface.
//
// The assertions worth having are the ones that guard the states the design
// says the list must survive, because each is an ordinary row in a real
// library rather than an edge case: nothing in v1.11 has a name, a workflow can
// outlive every picture it made, a recipe's model names can be deleted, and
// three of the four empty states are the first thing a new user sees.
//
// The 159-variant family is measured here too. That row is the reason the list
// opens at topology level at all, so "opening it costs one request and draws
// what it was given" is a claim this suite makes rather than a hope.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listWorkflows = vi.fn();
const listWorkflowVariants = vi.fn();
const listWorkflowPictures = vi.fn();
const getWorkflowGraph = vi.fn();

vi.mock("../../api/workflows", () => ({
  listWorkflows: (...args) => listWorkflows(...args),
  listWorkflowVariants: (...args) => listWorkflowVariants(...args),
  listWorkflowPictures: (...args) => listWorkflowPictures(...args),
  getWorkflowGraph: (...args) => getWorkflowGraph(...args),
}));

import WorkflowShelf from "./WorkflowShelf.vue";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      // The real menu needs Vuetify's overlay provider, which this suite does
      // not install. This stub renders the activator and the panel inline,
      // which is all the assertions here need.
      "v-menu": {
        template: "<div><slot name='activator' :props='{}' /><slot /></div>",
      },
    },
  },
};

function topology(overrides = {}) {
  return {
    topology_hash: "a".repeat(64),
    hash_version: "v1",
    node_count: 47,
    first_seen_at: "2026-08-01T00:00:00Z",
    variants: 3,
    pictures: 1075,
    last_used: "2026-08-20T00:00:00Z",
    assets: [
      { widget: "ckpt_name", name: "realvisxlv40.safetensors" },
      { widget: "lora_name", name: "add_detail_xl.safetensors" },
    ],
    ...overrides,
  };
}

async function mountShelf() {
  const wrapper = mount(WorkflowShelf, globalOpts);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listWorkflows.mockReset().mockResolvedValue({
    scan: { pictures: 28172, scanned: 28172 },
    workflows: [topology()],
  });
  listWorkflowVariants.mockReset().mockResolvedValue([]);
  listWorkflowPictures.mockReset().mockResolvedValue([]);
  getWorkflowGraph.mockReset();
});

describe("the list", () => {
  it("names an unnamed workflow from its own graph", async () => {
    // Naming is a later step, so this is EVERY row in this release. A row that
    // rendered a blank here would be unusable rather than merely plain.
    const wrapper = await mountShelf();
    expect(textOf(wrapper)).toContain("realvisxlv40, 1 LoRA, 47 nodes");
    expect(textOf(wrapper)).toContain("not named");
  });

  it("says none kept rather than 0 for a workflow that outlived its pictures", async () => {
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [topology({ pictures: 0 })],
    });
    const wrapper = await mountShelf();
    expect(textOf(wrapper)).toContain("none kept");
  });

  it("says the model names are missing rather than drawing a blank cell", async () => {
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [topology({ assets: [] })],
    });
    const wrapper = await mountShelf();
    expect(textOf(wrapper)).toContain("no model names");
    // …and the row is still identifiable, which is the point of forgetting a
    // name being a row delete rather than a rewrite of the graph.
    expect(textOf(wrapper)).toContain("47 nodes");
  });

  it("counts families and variants in the subtitle", async () => {
    const wrapper = await mountShelf();
    expect(textOf(wrapper)).toContain("1 family · 3 variants");
  });
});

describe("the empty states", () => {
  it("distinguishes not looked yet from looked and found nothing", async () => {
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 0 },
      workflows: [],
    });
    let wrapper = await mountShelf();
    expect(textOf(wrapper)).toContain("Nothing read yet");

    setActivePinia(createPinia());
    listWorkflows.mockResolvedValue({
      scan: { pictures: 19943, scanned: 19943 },
      workflows: [],
    });
    wrapper = await mountShelf();
    expect(textOf(wrapper)).toContain("No workflows in this library");
  });

  it("says a half-read library is unfinished, not broken", async () => {
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 8412 },
      workflows: [],
    });
    const wrapper = await mountShelf();
    expect(textOf(wrapper)).toContain("Reading your pictures");
    // No error hue and no alert: the list is correct and nearly empty, which is
    // the rule all four states share.
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });
});

describe("the expansion", () => {
  it("fetches a row's variants once, on the first open", async () => {
    listWorkflowVariants.mockResolvedValue([
      {
        structural_hash: "b".repeat(64),
        node_count: 47,
        first_seen_at: "2026-08-01T00:00:00Z",
        pictures: 1031,
        last_used: "2026-08-20T00:00:00Z",
        assets: [{ widget: "ckpt_name", name: "realvisxlv40.safetensors" }],
      },
    ]);
    const wrapper = await mountShelf();
    const twisty = wrapper.find(".wfshelf-twisty");
    expect(twisty.exists()).toBe(true);

    await twisty.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(listWorkflowVariants).toHaveBeenCalledTimes(1);
    expect(wrapper.findAll(".wfshelf-row--variant")).toHaveLength(1);

    // Closed and opened again: the rows are already in hand, so nothing is
    // asked for a second time.
    await twisty.trigger("click");
    await twisty.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(listWorkflowVariants).toHaveBeenCalledTimes(1);
  });

  it("draws the worst family in the library without asking for it twice", async () => {
    // 159 variants is measured, not invented: it is the largest family in the
    // owner's three libraries, and it is the row this whole shape exists for.
    const many = Array.from({ length: 159 }, (_, index) => ({
      structural_hash: String(index).padStart(64, "0"),
      node_count: 71,
      first_seen_at: "2026-08-01T00:00:00Z",
      pictures: index,
      last_used: "2026-08-20T00:00:00Z",
      assets: [{ widget: "lora_name", name: `character_${index}.safetensors` }],
    }));
    listWorkflowVariants.mockResolvedValue(many);
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [topology({ variants: 159 })],
    });
    const wrapper = await mountShelf();
    await wrapper.find(".wfshelf-twisty").trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(listWorkflowVariants).toHaveBeenCalledTimes(1);
    expect(wrapper.findAll(".wfshelf-row--variant")).toHaveLength(159);
  });

  it("offers no disclosure on a workflow with one variant", async () => {
    // Opening a single-variant workflow would restate the row above it.
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [topology({ variants: 1 })],
    });
    const wrapper = await mountShelf();
    expect(wrapper.find(".wfshelf-twisty--empty").exists()).toBe(true);
    expect(wrapper.find("button.wfshelf-twisty").exists()).toBe(false);
  });
});

describe("the view axes", () => {
  it("re-sorts without going back to the server", async () => {
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [
        topology({
          topology_hash: "a".repeat(64),
          pictures: 10,
          node_count: 90,
        }),
        topology({
          topology_hash: "b".repeat(64),
          pictures: 900,
          node_count: 12,
        }),
      ],
    });
    const wrapper = await mountShelf();
    expect(listWorkflows).toHaveBeenCalledTimes(1);

    const rows = () =>
      wrapper
        .findAll(".wfshelf-row")
        .map((row) => row.attributes("data-row-key"));
    expect(rows()[0]).toBe("b".repeat(64));

    wrapper.vm.store.setView({ sortKey: "nodes" });
    await wrapper.vm.$nextTick();
    expect(rows()[0]).toBe("a".repeat(64));
    expect(listWorkflows).toHaveBeenCalledTimes(1);
  });

  it("narrows to what is still in use without going back to the server", async () => {
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [
        topology({ topology_hash: "a".repeat(64), pictures: 10 }),
        topology({ topology_hash: "b".repeat(64), pictures: 0 }),
      ],
    });
    const wrapper = await mountShelf();
    wrapper.vm.store.setView({ show: "unused" });
    await wrapper.vm.$nextTick();
    const rows = wrapper.findAll(".wfshelf-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].attributes("data-row-key")).toBe("b".repeat(64));
    expect(listWorkflows).toHaveBeenCalledTimes(1);
  });
});

describe("the keyboard", () => {
  it("walks THROUGH an open workflow, not over it", async () => {
    // A variant is a child row of the workflow above it, which is the whole of
    // what `treegrid` means here. Down landing on the next workflow would step
    // over rows the reader can see, and 159 of them on the worst family.
    listWorkflowVariants.mockResolvedValue([
      {
        structural_hash: "b".repeat(64),
        node_count: 47,
        first_seen_at: "2026-08-01T00:00:00Z",
        pictures: 1031,
        last_used: "2026-08-20T00:00:00Z",
        assets: [],
      },
    ]);
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [
        topology({ topology_hash: "a".repeat(64), pictures: 900 }),
        topology({ topology_hash: "c".repeat(64), pictures: 1, variants: 1 }),
      ],
    });
    const wrapper = await mountShelf();
    await wrapper.find("button.wfshelf-twisty").trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    const keys = wrapper
      .findAll("[data-row-key]")
      .map((el) => el.attributes("data-row-key"));
    expect(keys).toEqual([
      "a".repeat(64),
      `${"a".repeat(64)}:${"b".repeat(64)}`,
      "c".repeat(64),
    ]);

    // The variant is a tab stop of its own, so Down can reach it at all.
    const variantRow = wrapper.find(".wfshelf-row--variant");
    expect(variantRow.attributes("data-row-key")).toBe(
      `${"a".repeat(64)}:${"b".repeat(64)}`,
    );
    await variantRow.trigger("keydown", { key: "ArrowLeft" });
    await wrapper.vm.$nextTick();
    // Left from inside an expansion closes it, which is the only way back out
    // of a 159-row one without walking every row.
    expect(wrapper.findAll(".wfshelf-row--variant")).toHaveLength(0);
  });
});

describe("exporting a workflow's graph", () => {
  // The verb had no test and shipped broken: it fell back to the TOPOLOGY hash
  // for a single-variant row, which addresses no recipe, so the commonest kind
  // of row in the list answered 404 while the menu promised a file.
  beforeEach(() => {
    // jsdom has no object-URL plumbing and no navigation, so the parts of the
    // download that are the browser's are stubbed. What is under test is which
    // hash reaches the API.
    URL.createObjectURL = vi.fn(() => "blob:stub");
    URL.revokeObjectURL = vi.fn();
    HTMLAnchorElement.prototype.click = vi.fn();
  });

  it("exports the RECIPE of a single-variant workflow, never its topology hash", async () => {
    listWorkflows.mockResolvedValue({
      scan: { pictures: 28172, scanned: 28172 },
      workflows: [topology({ variants: 1 })],
    });
    listWorkflowVariants.mockResolvedValue([
      {
        structural_hash: "b".repeat(64),
        node_count: 47,
        first_seen_at: "2026-08-01T00:00:00Z",
        pictures: 3,
        last_used: null,
        assets: [],
      },
    ]);
    getWorkflowGraph.mockResolvedValue({
      structural_hash: "b".repeat(64),
      document: { nodes: {} },
      runnable: false,
    });
    const wrapper = await mountShelf();

    await wrapper.vm.openRowMenu(wrapper.vm.store.visibleRows[0], {
      clientX: 0,
      clientY: 0,
    });
    expect(wrapper.vm.canExport).toBe(true);
    await wrapper.vm.exportGraph();

    expect(getWorkflowGraph).toHaveBeenCalledWith("b".repeat(64));
    expect(getWorkflowGraph).not.toHaveBeenCalledWith("a".repeat(64));
  });

  it("refuses on a workflow with several variants rather than picking one", async () => {
    const wrapper = await mountShelf();
    await wrapper.vm.openRowMenu(wrapper.vm.store.visibleRows[0], {
      clientX: 0,
      clientY: 0,
    });
    // The seeded row has three variants and nothing says which is meant.
    expect(wrapper.vm.canExport).toBe(false);
    await wrapper.vm.exportGraph();
    expect(getWorkflowGraph).not.toHaveBeenCalled();
  });
});
