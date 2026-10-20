// The Templates tab's state (#2000): which templates could run here, what the
// filters leave, and the order the readiness sort puts them in.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const listTemplates = vi.fn();
const addTemplates = vi.fn();
vi.mock("../api/comfyui", () => ({
  listTemplates: (...args) => listTemplates(...args),
  addTemplates: (...args) => addTemplates(...args),
}));

import {
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
  template("ready", { files: [file("ae.safetensors", true)], usage: 5 }),
  template("partner", { source: "partner", usage: 99 }),
  template("one-missing", {
    media_type: "video",
    tags: ["Image to Video"],
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

describe("the Templates tab's list", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    listTemplates.mockReset();
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

  it("filters by media, source, readiness and words in tags and file names", async () => {
    const store = await loaded();
    store.setFilters({ media: "video" });
    expect(keys(store)).toEqual(["one-missing"]);
    store.clearFilters();
    store.setFilters({ readiness: "missing" });
    expect(keys(store)).toEqual(["one-missing", "also-missing"]);
    store.clearFilters();
    store.setFilters({ name: "image to video" });
    expect(keys(store)).toEqual(["one-missing"]);
    store.setFilters({ name: "wan.safetensors" });
    expect(keys(store)).toEqual(["one-missing"]);
    store.clearFilters();
    // Asking for partner templates by source shows them whatever the toggle.
    store.setFilters({ source: "partner" });
    expect(keys(store)).toEqual(["partner"]);
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
