// The Templates tab's state (#2000): which templates could run here, what the
// filters leave, and the order the readiness sort puts them in.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

const listTemplates = vi.fn();
const addTemplates = vi.fn();
vi.mock("../api/comfyui", () => ({
  listTemplates: (...args) => listTemplates(...args),
  addTemplates: (...args) => addTemplates(...args),
}));

import {
  isPixlstashPack,
  templateFileLetter,
  templateReadiness,
  useWorkflowTemplatesStore,
} from "./useWorkflowTemplatesStore";

const file = (name, present, directory = "vae") => ({ name, present, directory });

const template = (name, extra = {}) => ({
  key: `default/${name}`,
  module: "default",
  name,
  source: "builtin",
  title: name,
  media_type: "image",
  tags: [],
  model_names: [],
  files: [],
  ...extra,
});

const LIST = [
  // Sorts before "one-missing" by name, so the readiness order is not the
  // alphabet's: two missing files come after one.
  template("also-missing", {
    files: [file("a.safetensors", false), file("b.safetensors", false)],
  }),
  template("ready", {
    files: [file("ae.safetensors", true)],
    usage: 5,
    size: 900,
    categories: ["Image", "Product & Ads"],
    model_names: ["Flux"],
  }),
  template("partner", { source: "partner", usage: 99, categories: ["Image"] }),
  template("one-missing", {
    // `media_type` is the picture's, as ComfyUI's index has it: a video
    // template is found by its category.
    media_type: "image",
    categories: ["Video"],
    tags: ["Image to Video"],
    model_names: ["Wan"],
    size: 100,
    files: [file("wan.safetensors", false, "diffusion_models"), file("t5.safetensors", true)],
  }),
  template("pack", {
    key: "Pack/pack",
    module: "Pack",
    source: "pack",
    pack_loaded: false,
  }),
  template("nothing-to-download"),
];

async function loaded() {
  listTemplates.mockResolvedValue({
    comfyui_url: "http://127.0.0.1:8188",
    reachable: true,
    templates: LIST,
  });
  const store = useWorkflowTemplatesStore();
  await store.fetchTemplates();
  return store;
}

const keys = (store) => store.sortedTemplates.map((t) => t.name);

describe("which templates could run here", () => {
  it("reads readiness off the source, the pack and the shelf", () => {
    expect(LIST.map(templateReadiness)).toEqual([
      "missing",
      "ready",
      "partner",
      "missing",
      "pack_not_loaded",
      // Names no file, and is no partner template: nothing stands in its way.
      "ready",
    ]);
  });

  it("marks a base model by its own initial and the rest by their kind", () => {
    expect(templateFileLetter(file("flux1-krea.safetensors", true, "diffusion_models"))).toBe("F");
    expect(templateFileLetter(file("clip_l.safetensors", true, "text_encoders"))).toBe("T");
    expect(templateFileLetter(file("ae.safetensors", true, "vae"))).toBe("V");
    expect(templateFileLetter(file("x.safetensors", true, "loras"))).toBe("L");
    expect(templateFileLetter(file("x.pth", true, "sams"))).toBe("S");
  });
});

describe("PixlStash's own pack", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  const pack = (module) => ({ key: `${module}/x`, module, source: "pack" });

  it("is recognised by every folder name it installs under, and only those", () => {
    // A git clone, the registry or Manager, a GitHub zip.
    for (const module of ["ComfyUI-PixlStash", "comfyui-pixlstash", "ComfyUI-PixlStash-main"]) {
      expect(isPixlstashPack(pack(module))).toBe(true);
    }
    // Another pack whose name only starts the same is not ours.
    expect(isPixlstashPack(pack("ComfyUI-PixlStashFoo"))).toBe(false);
    expect(isPixlstashPack(pack("WanVideoWrapper"))).toBe(false);
    // A built-in that happens to share the name is not a pack.
    expect(isPixlstashPack({ ...pack("ComfyUI-PixlStash"), source: "builtin" })).toBe(false);
  });

  it("is named PixlStash and listed first among the packs, under its own id", () => {
    const store = useWorkflowTemplatesStore();
    store.templates = [
      pack("Alpha"),
      pack("ComfyUI-PixlStash"),
      { ...pack("ComfyUI-PixlStash"), key: "ComfyUI-PixlStash/y" },
      pack("Zeta"),
    ];
    expect(
      store.filterOptions.category.map((o) => [o.id, o.label, o.count, o.pixlstash]),
    ).toEqual([
      ["extension-ComfyUI-PixlStash", "PixlStash", 2, true],
      ["extension-Alpha", "Alpha", 1, false],
      ["extension-Zeta", "Zeta", 1, false],
    ]);
    store.setFilters({ category: ["extension-ComfyUI-PixlStash"] });
    expect(store.filterChips.map((chip) => chip.value)).toContain("PixlStash");
  });
});

describe("the Templates tab's list", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    listTemplates.mockReset();
  });

  it("remembers the sort and filters for next time, but not the name text", async () => {
    const store = useWorkflowTemplatesStore();
    store.sortKey = "newest";
    store.setFilters({
      category: ["Video", "Image"],
      model: ["Wan"],
      useCase: ["Image to Video"],
      runsOn: ["comfyui"],
      readiness: ["missing"],
      showPartner: true,
      name: "flux",
    });
    await nextTick();
    expect(
      JSON.parse(localStorage.getItem("pixlstash:workflowTemplatesView")),
    ).not.toHaveProperty("name");
    setActivePinia(createPinia());
    const next = useWorkflowTemplatesStore();
    expect(next.sortKey).toBe("newest");
    expect(next.filters).toEqual({
      name: "",
      category: ["Video", "Image"],
      model: ["Wan"],
      useCase: ["Image to Video"],
      runsOn: ["comfyui"],
      readiness: ["missing"],
      showPartner: true,
    });
  });

  it("comes back from a session reset as a fresh start would", async () => {
    const store = useWorkflowTemplatesStore();
    store.sortKey = "newest";
    store.setFilters({ model: ["Wan"], name: "flux" });
    await nextTick();
    localStorage.clear();
    store.reset();
    expect(store.sortKey).toBe("readiness");
    expect(store.filters.model).toEqual([]);
    expect(store.filters.name).toBe("");
  });

  it("starts from the defaults on a blob it cannot trust", () => {
    localStorage.setItem(
      "pixlstash:workflowTemplatesView",
      JSON.stringify({
        v: 3,
        sortKey: "bogus",
        runsOn: ["nowhere", "api"],
        readiness: ["missing", 7],
        model: "not a list",
      }),
    );
    const store = useWorkflowTemplatesStore();
    expect(store.sortKey).toBe("readiness");
    expect(store.filters.runsOn).toEqual(["api"]);
    expect(store.filters.readiness).toEqual(["missing"]);
    expect(store.filters.model).toEqual([]);
    localStorage.setItem("pixlstash:workflowTemplatesView", "{not json");
    setActivePinia(createPinia());
    expect(useWorkflowTemplatesStore().filters.readiness).toEqual([]);
  });

  it("sorts by readiness: ready first, one that names its files ahead of one that names none, then fewest missing; partner hidden by default", async () => {
    const store = await loaded();
    expect(keys(store)).toEqual([
      "ready",
      "nothing-to-download",
      "one-missing",
      "also-missing",
      "pack",
    ]);
    // The default that hides them is a chip, with the count it hides.
    expect(store.filterChips.find((chip) => chip.key === "partner").value).toBe(
      "hidden (1)",
    );
    expect(store.activeFilterCount).toBe(0);
  });

  it("shows partner templates when the chip is removed", async () => {
    const store = await loaded();
    store.filterChips.find((chip) => chip.key === "partner").remove();
    expect(keys(store)).toContain("partner");
    expect(store.filterChips.some((chip) => chip.key === "partner")).toBe(false);
  });

  it("filters as ComfyUI does: category, model, use case, runs on", async () => {
    const store = await loaded();
    store.setFilters({ category: ["Video"] });
    expect(keys(store)).toEqual(["one-missing"]);
    // Filed under two categories, found under either.
    store.setFilters({ category: ["Product & Ads"] });
    expect(keys(store)).toEqual(["ready"]);
    store.setFilters({ category: ["extension-Pack"] });
    expect(keys(store)).toEqual(["pack"]);
    store.setFilters({ category: ["partner-nodes"] });
    expect(keys(store)).toEqual(["partner"]);
    store.clearFilters();
    store.setFilters({ model: ["Flux"] });
    expect(keys(store)).toEqual(["ready"]);
    store.clearFilters();
    store.setFilters({ useCase: ["Image to Video"] });
    expect(keys(store)).toEqual(["one-missing"]);
    store.clearFilters();
    store.setFilters({ runsOn: ["api"] });
    expect(keys(store)).toEqual(["partner"]);
    expect(store.filterChips.map((chip) => chip.value)).toEqual([
      "External or Remote API",
    ]);
    // Each list's rows, with what each holds: categories in ComfyUI's order,
    // then Partner Nodes and the packs.
    expect(store.filterOptions.category.map((o) => [o.label, o.count])).toEqual([
      ["Image", 2],
      ["Product & Ads", 1],
      ["Video", 1],
      ["Partner Nodes", 1],
      ["Pack", 1],
    ]);
    expect(store.filterOptions.runsOn.map((o) => [o.label, o.count])).toEqual([
      ["ComfyUI", 5],
      ["External or Remote API", 1],
    ]);
  });

  it("keeps a template matching any value of a filter, and every filter at once", async () => {
    const store = await loaded();
    store.setFilters({ category: ["Video", "Product & Ads"] });
    expect(keys(store)).toEqual(["ready", "one-missing"]);
    store.setFilters({ model: ["Wan"] });
    expect(keys(store)).toEqual(["one-missing"]);
    // One chip per value, each taking out only its own.
    expect(store.filterChips.map((chip) => chip.value)).toEqual([
      "Video",
      "Product & Ads",
      "Wan",
      "hidden (1)",
    ]);
    store.filterChips[0].remove();
    expect(store.filters.category).toEqual(["Product & Ads"]);
    expect(keys(store)).toEqual([]);
  });

  it("filters by readiness and words in tags and file names", async () => {
    const store = await loaded();
    store.setFilters({ readiness: ["missing"] });
    expect(keys(store)).toEqual(["one-missing", "also-missing"]);
    store.clearFilters();
    store.setFilters({ name: "image to video" });
    expect(keys(store)).toEqual(["one-missing"]);
    store.setFilters({ name: "wan.safetensors" });
    expect(keys(store)).toEqual(["one-missing"]);
    store.clearFilters();
    // Asking for partner templates by readiness, which would otherwise always come back empty.
    store.setFilters({ readiness: ["partner"] });
    expect(keys(store)).toEqual(["partner"]);
    // Nor does the chip then claim they are hidden.
    expect(store.filterChips.map((chip) => chip.key)).toEqual(["readiness:partner"]);
  });

  it("sorts by model size, smallest first, a template that gives none last", async () => {
    const store = await loaded();
    store.sortKey = "size";
    expect(keys(store).slice(0, 2)).toEqual(["one-missing", "ready"]);
  });

  it("sorts by use", async () => {
    const store = await loaded();
    store.setFilters({ showPartner: true });
    store.sortKey = "popular";
    expect(keys(store).slice(0, 2)).toEqual(["partner", "ready"]);
  });

  it("reads once, and again only when asked to", async () => {
    const store = await loaded();
    await store.fetchTemplates();
    expect(listTemplates).toHaveBeenCalledTimes(1);
    await store.fetchTemplates({ refresh: true });
    expect(listTemplates).toHaveBeenLastCalledWith({ refresh: true });
  });

  it("re-reads a list it holds when a model arrives, without asking ComfyUI", async () => {
    // Never read: the tab reads it when it is shown.
    await useWorkflowTemplatesStore().onModelsArrived();
    expect(listTemplates).not.toHaveBeenCalled();
    const store = await loaded();
    await store.onModelsArrived();
    expect(listTemplates.mock.calls).toEqual([[{ refresh: false }], [{ refresh: false }]]);
  });

  it("re-reads after a read already out, which may predate the arrival", async () => {
    let land;
    listTemplates.mockReturnValueOnce(new Promise((resolve) => (land = resolve)));
    listTemplates.mockResolvedValue({ templates: [] });
    const store = useWorkflowTemplatesStore();
    const first = store.fetchTemplates();
    const again = store.fetchTemplates({ reread: true });
    land({ templates: [] });
    await Promise.all([first, again]);
    expect(listTemplates.mock.calls).toEqual([[{ refresh: false }], [{ refresh: false }]]);
  });

  it("keeps the list it has when a re-read finds ComfyUI silent", async () => {
    const store = await loaded();
    const before = store.templates;
    listTemplates.mockResolvedValue({ reachable: false, detail: "refused" });
    await store.onModelsArrived();
    expect(store.templates).toBe(before);
    expect(store.reachable).toBe(true);
  });

  it("asks again on a plain read while ComfyUI did not answer", async () => {
    listTemplates.mockResolvedValue({ reachable: false, detail: "refused" });
    const store = useWorkflowTemplatesStore();
    await store.fetchTemplates();
    await store.fetchTemplates();
    expect(listTemplates).toHaveBeenCalledTimes(2);
    expect(store.reachable).toBe(false);
  });

  it("makes a re-read asked for during a plain read, once that one lands", async () => {
    let land;
    listTemplates.mockReturnValueOnce(new Promise((resolve) => (land = resolve)));
    listTemplates.mockResolvedValue({ templates: [] });
    const store = useWorkflowTemplatesStore();
    const plain = store.fetchTemplates();
    const again = store.fetchTemplates({ refresh: true });
    land({ templates: [] });
    await Promise.all([plain, again]);
    expect(listTemplates.mock.calls).toEqual([[{ refresh: false }], [{ refresh: true }]]);
  });

  it("applies nothing from a read that was in flight when the session reset", async () => {
    let land;
    listTemplates.mockReturnValueOnce(new Promise((resolve) => (land = resolve)));
    const store = useWorkflowTemplatesStore();
    const read = store.fetchTemplates();
    store.reset();
    land({ comfyui_url: "http://127.0.0.1:8188", templates: [{ key: "default/old" }] });
    await read;
    expect(store.comfyuiUrl).toBe("");
    expect([store.loaded, store.templates]).toEqual([false, []]);
    // And the next read is a read of its own, not the stale one.
    listTemplates.mockResolvedValue({ templates: [{ key: "default/new" }] });
    await store.fetchTemplates();
    expect(store.templates.map((t) => t.key)).toEqual(["default/new"]);
  });

  it("lets a new session add while an Add from the old one is still out", async () => {
    const store = await loaded();
    let land;
    addTemplates.mockReturnValueOnce(new Promise((resolve) => (land = resolve)));
    const stale = store.add([{ module: "default", name: "ready" }]);
    expect(store.adding).toBe(true);
    store.reset();
    expect(store.adding).toBe(false);
    addTemplates.mockResolvedValue({ added: [{ workflow_id: "manual:new" }], failed: [] });
    const fresh = store.add([{ module: "default", name: "ready" }]);
    land({ added: [{ workflow_id: "manual:old" }], failed: [] });
    // The old answer goes nowhere; the new one is the view's to land on.
    expect(await stale).toBe(null);
    expect((await fresh).added[0].workflow_id).toBe("manual:new");
    expect(store.adding).toBe(false);
  });

  it("keeps what ComfyUI's silence says rather than an empty list", async () => {
    listTemplates.mockResolvedValue({
      comfyui_url: "http://127.0.0.1:8188",
      reachable: false,
      detail: "Nothing is answering for templates at http://127.0.0.1:8188.",
    });
    const store = useWorkflowTemplatesStore();
    await store.fetchTemplates();
    expect(store.reachable).toBe(false);
    expect(store.comfyuiUrl).toBe("http://127.0.0.1:8188");
  });

  it("adds the selection by module and name", async () => {
    const store = await loaded();
    store.selectRange(["default/ready", "Pack/pack"]);
    addTemplates.mockResolvedValue({ added: [], failed: [] });
    await store.add();
    expect(addTemplates.mock.calls[0][0].map((t) => [t.module, t.name])).toEqual([
      ["default", "ready"],
      ["Pack", "pack"],
    ]);
  });
});
