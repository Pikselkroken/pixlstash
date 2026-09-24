// Edit LoRAs (#1478): what the owner sees in the list is exactly what the
// save sends.
//
// * A delete strikes the row through and offers Restore; the entry is then
//   ABSENT from the PUT, which is how the route reads a delete.
// * A reorder, by keyboard as much as by drag, is the order of `entries`.
// * Add appends one row at the end, and only a picked LoRA is sent.
// * Save… is a dry run, and its `changes` are what the second step lists;
//   only "Save as a new workflow" writes.
// * `editable: false` opens read-only, with the planner's sentence.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getLoraChain = vi.fn();
const saveLoraChain = vi.fn();
vi.mock("../../api/workflows", () => ({
  getLoraChain: (...args) => getLoraChain(...args),
  saveLoraChain: (...args) => saveLoraChain(...args),
}));
const listAdapters = vi.fn();
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { useNoticeStore } from "../../stores/useNoticeStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import EditLorasDialog from "./EditLorasDialog.vue";

const KEY = "a".repeat(64);
const NEW_KEY = "e".repeat(64);

function loader(nodeId, filename, strength, overrides = {}) {
  const base = filename.split("/").pop();
  return {
    node_id: nodeId,
    class_type: "LoraLoader",
    filename,
    name: base.replace(/\.[^.]+$/, ""),
    strength,
    strength_clip: strength,
    sha256: `sha-${nodeId}`,
    on_shelf: true,
    ...overrides,
  };
}

function chain(overrides = {}) {
  return {
    workflow_key: KEY,
    editable: true,
    refusal: null,
    source: {
      node_id: "4",
      class_type: "CheckpointLoaderSimple",
      outputs: ["MODEL", "CLIP"],
    },
    sink: {
      summary: "KSampler #7 reads model · 2 text encoders read clip",
      consumers: [
        { node_id: "7", class_type: "KSampler", field: "model", type: "MODEL" },
      ],
    },
    loaders: [
      loader("14", "sub/lightning-8step.safetensors", 1.0),
      loader("22", "neon-rain-v2.safetensors", 0.85),
      loader("31", "film-grain-35mm.safetensors", 0.4),
      // Mixed case on purpose: the shelf match is case-folded and every other
      // fixture is lowercase.
      loader("33", "Hairstyle-V3.safetensors", 0.6, {
        sha256: null,
        on_shelf: false,
      }),
    ],
    added_loader_class: "LoraLoader",
    ...overrides,
  };
}

async function mountDialog(props = {}) {
  const wrapper = mount(EditLorasDialog, {
    props: {
      open: true,
      workflowKey: KEY,
      cardName: "SDXL + face detailer",
      pictureCount: 184,
      ...props,
    },
    global: { stubs: { Tooltip: true, teleport: true } },
    attachTo: document.body,
  });
  await flushPromises();
  return wrapper;
}

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

function rowNames(wrapper) {
  return wrapper
    .findAll(".eld-row")
    .map((row) => {
      const name = row.find(".eld-name-text");
      const words = name.exists() ? name.text().trim() : "";
      return row.find(".eld-tag").exists() ? `${words} new` : words;
    });
}

/** A button's words, without the icon stub's `mdi-…` text. */
function labelOf(entry) {
  const own = entry.find(".app-btn__label");
  return (own.exists() ? own.text() : entry.text()).trim();
}

function button(wrapper, label) {
  const found = wrapper
    .findAll("button")
    .find((entry) => labelOf(entry) === label);
  if (!found) throw new Error(`no button reading ${label}`);
  return found;
}

function deleteButton(wrapper, index) {
  return wrapper.findAll(".eld-row")[index].find("[data-focus='delete']");
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  document.body.innerHTML = "";
  getLoraChain.mockResolvedValue(chain());
  listAdapters.mockResolvedValue([
    { filename: "skin-detail-xl.safetensors", sha256: "sha-skin", display_name: "" },
    { filename: "no-digest.safetensors", sha256: null },
  ]);
  saveLoraChain.mockImplementation(async (_key, body) =>
    body.dry_run
      ? {
          dry_run: true,
          name: null,
          workflow_key: null,
          changes: [
            { kind: "deleted", node_id: "33", text: "Loader #33 deleted: hairstyle-v3" },
            { kind: "rewired", node_id: "7", text: "#7 KSampler and 2 text encoders rewired" },
          ],
        }
      : {
          dry_run: false,
          name: "SDXL + face detailer (edited).json",
          workflow_key: NEW_KEY,
          changes: [],
        },
  );
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("the chain as read", () => {
  it("lists the loaders between the two rails, in apply order, with strengths", async () => {
    const wrapper = await mountDialog();
    expect(getLoraChain).toHaveBeenCalledWith(KEY);
    expect(wrapper.find("[data-testid='eld-source']").text()).toContain(
      "CheckpointLoaderSimple",
    );
    expect(wrapper.find("[data-testid='eld-sink']").text()).toContain(
      "KSampler #7 reads model",
    );
    expect(rowNames(wrapper)).toEqual([
      "lightning-8step",
      "neon-rain-v2",
      "film-grain-35mm",
      // The file the shelf cannot name is shown as the FILE.
      "Hairstyle-V3.safetensors",
    ]);
    const strengths = wrapper
      .findAll(".eld-row input[type=number]")
      .map((input) => input.element.value);
    expect(strengths).toEqual(["1.00", "0.85", "0.40", "0.60"]);
  });

  it("flags the loader that is not on the shelf by its file name", async () => {
    const wrapper = await mountDialog();
    const said = textOf(wrapper);
    // The owner's wording: the file, and that it is missing. Not "a run
    // ignores that loader", which is false - ComfyUI loads it by file name.
    expect(said).toContain(
      "Hairstyle-V3.safetensors is missing from your model shelf.",
    );
    expect(said).not.toContain("ignores that loader");
    // And says what saving does to the original.
    expect(said).toContain(
      "Saving writes a new workflow. SDXL + face detailer and its 184 pictures stay as they are.",
    );
    expect(textOf(wrapper)).toContain("No changes");
    expect(button(wrapper, "Save…").attributes("disabled")).toBeDefined();
  });
});

describe("delete and restore", () => {
  it("sends an untouched strength back exactly as read, not as displayed", async () => {
    getLoraChain.mockResolvedValue(
      chain({
        loaders: [
          loader("14", "a.safetensors", 0.855),
          loader("22", "b.safetensors", 1.0),
        ],
      }),
    );
    const wrapper = await mountDialog();
    await deleteButton(wrapper, 1).trigger("click");
    await flushPromises();
    await button(wrapper, "Save…").trigger("click");
    await flushPromises();
    // Shown as 0.86 (two decimals); sending that would re-weight a loader
    // the owner never touched.
    expect(saveLoraChain.mock.calls[0][1].entries).toEqual([
      { node_id: "14", strength: 0.855 },
    ]);
  });

  it("strikes the row through with Restore, and leaves it out of the PUT", async () => {
    const wrapper = await mountDialog();
    await deleteButton(wrapper, 3).trigger("click");
    await flushPromises();

    const row = wrapper.findAll(".eld-row")[3];
    expect(row.classes()).toContain("eld-row--deleted");
    expect(row.find(".eld-pos").text()).toBe("—");
    expect(row.text()).toContain("Restore");
    // Focus lands on the Restore of the row it happened on.
    expect(document.activeElement?.textContent?.trim()).toBe("Restore");
    expect(textOf(wrapper)).toContain("1 change");

    await button(wrapper, "Save…").trigger("click");
    await flushPromises();
    const body = saveLoraChain.mock.calls[0][1];
    expect(body.dry_run).toBe(true);
    expect(body.entries.map((entry) => entry.node_id)).toEqual(["14", "22", "31"]);
  });

  it("puts a restored row back where it was", async () => {
    const wrapper = await mountDialog();
    await deleteButton(wrapper, 1).trigger("click");
    await flushPromises();
    await button(wrapper, "Restore").trigger("click");
    await flushPromises();

    const row = wrapper.findAll(".eld-row")[1];
    expect(row.classes()).not.toContain("eld-row--deleted");
    expect(row.find(".eld-pos").text()).toBe("2");
    expect(textOf(wrapper)).toContain("No changes");
  });
});

describe("reordering by keyboard", () => {
  it("moves the focused row with Alt+↓, says so, and sends that order", async () => {
    const wrapper = await mountDialog();
    const handle = wrapper.findAll(".eld-row")[1].find("[data-focus='handle']");
    await handle.trigger("keydown", { key: "ArrowDown", altKey: true });
    await flushPromises();

    expect(rowNames(wrapper).slice(0, 3)).toEqual([
      "lightning-8step",
      "film-grain-35mm",
      "neon-rain-v2",
    ]);
    expect(wrapper.find("[role=status][aria-live=polite]").text()).toBe(
      "neon-rain-v2 moved to 3 of 4.",
    );
    // Focus follows the handle that moved, so a held Alt+↓ walks the list.
    expect(
      document.activeElement?.closest("[data-row]")?.getAttribute("data-row"),
    ).toBe("n:22");
    // A swap of neighbours is one change, as the owner made it.
    expect(textOf(wrapper)).toContain("1 change");

    await button(wrapper, "Save…").trigger("click");
    await flushPromises();
    expect(
      saveLoraChain.mock.calls[0][1].entries.map((entry) => entry.node_id),
    ).toEqual(["14", "31", "22", "33"]);
  });


  it("moves past a deleted row rather than swapping with it", async () => {
    const wrapper = await mountDialog();
    await deleteButton(wrapper, 2).trigger("click");
    await flushPromises();
    const handle = wrapper.findAll(".eld-row")[1].find("[data-focus='handle']");
    await handle.trigger("keydown", { key: "ArrowDown", altKey: true });
    await flushPromises();

    await button(wrapper, "Save…").trigger("click");
    await flushPromises();
    // 22 jumped the struck-through 31 and landed after 33: a real move the
    // save writes, not a swap with a row that is going anyway.
    expect(
      saveLoraChain.mock.calls[0][1].entries.map((entry) => entry.node_id),
    ).toEqual(["14", "33", "22"]);
  });

  it("does not move a row past the ends", async () => {
    const wrapper = await mountDialog();
    const handle = wrapper.findAll(".eld-row")[0].find("[data-focus='handle']");
    await handle.trigger("keydown", { key: "ArrowUp", altKey: true });
    await flushPromises();
    expect(rowNames(wrapper)[0]).toBe("lightning-8step");
    expect(textOf(wrapper)).toContain("No changes");
  });
});

describe("the chain as a graph", () => {
  it("titles each loader node with its class and id, and a new one with the class a save adds", async () => {
    const wrapper = await mountDialog();
    const titles = () =>
      wrapper.findAll(".eld-row .eld-node-title").map((title) => title.text());
    expect(titles()).toEqual([
      "LoraLoader #14",
      "LoraLoader #22",
      "LoraLoader #31",
      "LoraLoader #33",
    ]);
    await button(wrapper, "Add a LoRA").trigger("click");
    await flushPromises();
    expect(titles().at(-1)).toBe("LoraLoader");
    // One arrow into every loader, and one more into the reader.
    expect(wrapper.findAll(".eld-wire:not(.eld-wire--plain)")).toHaveLength(6);
  });
});

describe("Add a LoRA", () => {
  it("appends one picker row at the end, and sends only a picked LoRA", async () => {
    const wrapper = await mountDialog();
    await button(wrapper, "Add a LoRA").trigger("click");
    await flushPromises();

    const rows = wrapper.findAll(".eld-row");
    expect(rows).toHaveLength(5);
    const select = rows[4].find("select");
    // Only digested shelf rows can be added: the route adds by sha256.
    expect(select.findAll("option").map((option) => option.text())).toEqual([
      "Pick a LoRA from your shelf…",
      "skin-detail-xl",
    ]);
    // Nothing picked is not a change, and Add waits for it.
    expect(textOf(wrapper)).toContain("No changes");
    expect(button(wrapper, "Add a LoRA").attributes("disabled")).toBeDefined();

    await select.setValue("sha-skin");
    await flushPromises();
    expect(rowNames(wrapper)[4]).toBe("skin-detail-xl new");
    expect(textOf(wrapper)).toContain("1 change");

    await button(wrapper, "Save…").trigger("click");
    await flushPromises();
    expect(saveLoraChain.mock.calls[0][1].entries.at(-1)).toEqual({
      node_id: null,
      sha256: "sha-skin",
      strength: 1,
    });
  });

  it("says where the first loader of an empty chain goes, and which class", async () => {
    getLoraChain.mockResolvedValue(
      chain({
        loaders: [],
        source: { node_id: "4", class_type: "UNETLoader", outputs: ["MODEL"] },
        sink: {
          summary: "KSampler #9 reads model",
          consumers: [
            { node_id: "9", class_type: "KSampler", field: "model", type: "MODEL" },
          ],
        },
        added_loader_class: "LoraLoaderModelOnly",
      }),
    );
    const wrapper = await mountDialog({ cardName: "Character sheet" });
    // An empty chain is a wire between two nodes, and the button says the
    // loader is spliced into it; once a row stands it is an Add again.
    const labels = () => wrapper.findAll("button").map(labelOf);
    expect(labels()).not.toContain("Add a LoRA");
    // The sink comes after the button, so its name says which two nodes.
    expect(
      button(wrapper, "Insert LoRA between these nodes").attributes("aria-label"),
    ).toBe(
      "Insert LoRA between these nodes: UNETLoader #4 and KSampler #9",
    );
    await button(wrapper, "Insert LoRA between these nodes").trigger("click");
    await flushPromises();
    expect(labels()).toContain("Add a LoRA");
    expect(labels()).not.toContain("Insert LoRA between these nodes");
    expect(textOf(wrapper)).toContain(
      "The loader goes in after #4 UNETLoader, and the 1 input reading its MODEL is rewired to it. This graph has no CLIP source, so LoraLoaderModelOnly is used.",
    );
  });

  // Each of these keeps the button an Add, off the wire: the splice wording
  // claims two nodes it can insert between, and here one is missing or the
  // insert is not on offer.
  it.each([
    [
      // What the route sends when nothing reading the chain could be named:
      // a sink with no summary, not a missing sink.
      "no reader",
      { sink: { summary: null, consumers: [] } },
    ],
    ["no model source", { source: null }],
    ["a read-only chain", { editable: false, refusal: "Node 68 cannot be edited." }],
  ])("keeps Add a LoRA on an empty chain with %s", async (_case, overrides) => {
    getLoraChain.mockResolvedValue(chain({ loaders: [], ...overrides }));
    const wrapper = await mountDialog();
    expect(button(wrapper, "Add a LoRA").exists()).toBe(true);
  });

  it("says Nothing found reading the chain for a reader it could not name", async () => {
    getLoraChain.mockResolvedValue(
      chain({ loaders: [], sink: { summary: null, consumers: [] } }),
    );
    const wrapper = await mountDialog();
    expect(wrapper.find("[data-testid='eld-sink']").text()).toBe(
      "Nothing found reading the chain",
    );
  });

  it("keeps Add a LoRA when every loader is deleted, since the rows stay on screen", async () => {
    const wrapper = await mountDialog();
    for (let index = 0; index < 4; index += 1) {
      await deleteButton(wrapper, index).trigger("click");
      await flushPromises();
    }
    expect(wrapper.findAll(".eld-row--deleted")).toHaveLength(4);
    expect(button(wrapper, "Add a LoRA").exists()).toBe(true);
  });
});

describe("the second step", () => {
  async function toStepTwo() {
    const wrapper = await mountDialog();
    await deleteButton(wrapper, 3).trigger("click");
    await flushPromises();
    await button(wrapper, "Save…").trigger("click");
    await flushPromises();
    return wrapper;
  }

  it("names the new workflow and lists the dry run's changes", async () => {
    const wrapper = await toStepTwo();
    expect(wrapper.find("input[type=text]").element.value).toBe(
      "SDXL + face detailer (edited)",
    );
    const listed = wrapper
      .findAll("[data-testid='eld-changes'] li")
      .map((item) => item.text());
    expect(listed).toEqual([
      "Loader #33 deleted: hairstyle-v3",
      "#7 KSampler and 2 text encoders rewired",
    ]);
    expect(textOf(wrapper)).toContain(
      "The original keeps its 184 pictures. Nothing made before this edit changes meaning.",
    );
    // A dry run only: nothing is written until the second press.
    expect(saveLoraChain).toHaveBeenCalledTimes(1);
  });

  it("writes on 'Save as a new workflow' and moves the grid onto the new card", async () => {
    const wrapper = await toStepTwo();
    const store = useWorkflowsStore();
    const fetchCards = vi.spyOn(store, "fetchCards").mockResolvedValue();
    const select = vi.spyOn(store, "select");
    const notices = useNoticeStore();
    const push = vi.spyOn(notices, "push");

    await button(wrapper, "Save as a new workflow").trigger("click");
    await flushPromises();

    const [key, body] = saveLoraChain.mock.calls[1];
    expect(key).toBe(KEY);
    expect(body).toEqual({
      entries: [
        { node_id: "14", strength: 1 },
        { node_id: "22", strength: 0.85 },
        { node_id: "31", strength: 0.4 },
      ],
      name: "SDXL + face detailer (edited)",
      dry_run: false,
    });
    expect(fetchCards).toHaveBeenCalled();
    expect(select).toHaveBeenCalledWith(NEW_KEY);
    expect(push.mock.calls[0][0].text).toBe(
      "Saved “SDXL + face detailer (edited)” as a new workflow.",
    );
    expect(wrapper.emitted("saved")?.[0]?.[0]?.workflow_key).toBe(NEW_KEY);
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("stays open and says why when the save is refused", async () => {
    const wrapper = await toStepTwo();
    saveLoraChain.mockRejectedValueOnce({
      response: { status: 503, data: { detail: "ComfyUI did not answer." } },
    });
    await button(wrapper, "Save as a new workflow").trigger("click");
    await flushPromises();
    expect(wrapper.find("[role=alert]").text()).toContain("ComfyUI did not answer.");
    expect(wrapper.emitted("close")).toBeFalsy();
  });

  it("goes back to the list with the edit intact", async () => {
    const wrapper = await toStepTwo();
    await button(wrapper, "Back").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".eld-row")[3].classes()).toContain("eld-row--deleted");
  });
});

describe("read-only", () => {
  it("shows the planner's sentence and offers no edit", async () => {
    getLoraChain.mockResolvedValue(
      chain({
        editable: false,
        refusal: "ComfyUI did not answer, so the chain cannot be typed.",
      }),
    );
    const wrapper = await mountDialog();
    expect(textOf(wrapper)).toContain(
      "ComfyUI did not answer, so the chain cannot be typed.",
    );
    // The list is still shown, as read.
    expect(rowNames(wrapper)).toHaveLength(4);
    expect(deleteButton(wrapper, 0).attributes("disabled")).toBeDefined();
    expect(
      wrapper.findAll(".eld-row")[0].find("[data-focus='handle']").attributes("disabled"),
    ).toBeDefined();
    expect(
      wrapper.findAll(".eld-row")[0].find("[data-focus='handle']").attributes("draggable"),
    ).toBe("false");
    expect(button(wrapper, "Add a LoRA").attributes("disabled")).toBeDefined();
    expect(wrapper.findAll("button").some((b) => labelOf(b) === "Save…")).toBe(
      false,
    );
    expect(button(wrapper, "Close").exists()).toBe(true);
  });
});

describe("opened from Save as recipe", () => {
  it("opens with the named loader already deleted, matched case-folded", async () => {
    const wrapper = await mountDialog({ dropLora: "hairstyle-v3.safetensors" });
    const row = wrapper.findAll(".eld-row")[3];
    expect(row.classes()).toContain("eld-row--deleted");
    expect(row.text()).toContain("Restore");
    expect(textOf(wrapper)).toContain("1 change");
  });

  it("says so when the chain has no loader for it", async () => {
    const wrapper = await mountDialog({ dropLora: "elsewhere.safetensors" });
    expect(textOf(wrapper)).toContain(
      "This workflow has no loader for elsewhere.safetensors",
    );
    expect(textOf(wrapper)).toContain("No changes");
  });
});
