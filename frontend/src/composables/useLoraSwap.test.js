// The LoRA swap rules shared by the run panel, "Generate variants" and "Edit
// with ComfyUI" (#1310). One implementation, so the rules are pinned here once;
// each surface's own spec covers only its wiring.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";

const listAdapters = vi.fn();
vi.mock("../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));

// The read-only flag the insertion guard reads. Mocked rather than imported
// so a suite can set it without a session.
vi.mock("../utils/apiClient", () => ({ isReadOnly: { value: false } }));
import { isReadOnly } from "../utils/apiClient";

import { insertionSummary, slotKey, slotLabel, useLoraSwap } from "./useLoraSwap";

const SHA = "a".repeat(64);
const loader = (node_id, field = "lora_name", value) => ({
  node_id,
  class_type: "LoraLoader",
  field,
  value,
  by: "filename",
});

const flush = async () => {
  await nextTick();
  await new Promise((resolve) => setTimeout(resolve, 0));
};

beforeEach(() => {
  isReadOnly.value = false;
  listAdapters.mockReset().mockResolvedValue([
    { sha256: SHA, display_name: "Subject v2", base_model: "Flux" },
    { sha256: "b".repeat(64), display_name: "Anime style" },
    { sha256: null, display_name: "Still hashing" },
  ]);
});

describe("naming a slot", () => {
  it("keys a slot on its field and node, since a stacker shares one node", () => {
    expect(slotKey(loader("7", "lora_name_1"))).not.toBe(
      slotKey(loader("7", "lora_name_2")),
    );
    // A subgraph id carries a colon, so the separator must not be one.
    expect(slotKey(loader("75:83"))).toBe("lora_name@75:83");
  });

  it("reads as what it loads, and names the field only where it tells slots apart", () => {
    expect(slotLabel(loader("3", "lora_name", "detail.st"))).toBe("#3 detail.st");
    expect(slotLabel(loader("7", "lora_name_2", "b.st"))).toBe("#7 b.st · lora_name_2");
    // The share-token list carries no value; the class stands in.
    expect(slotLabel(loader("3"))).toBe("#3 LoraLoader");
  });
});

describe("the shelf list", () => {
  it("offers hashed adapters only, by name, with their base model", async () => {
    const swap = useLoraSwap(ref([loader("3")]));
    await flush();
    expect(swap.adapterOptions.value.map((o) => o.label)).toEqual([
      "Keep the workflow's own",
      "Anime style",
      "Subject v2 · Flux",
    ]);
  });

  it("is not read for a graph with no slot", async () => {
    useLoraSwap(ref([]));
    await flush();
    expect(listAdapters).not.toHaveBeenCalled();
  });

  it("is read once, and again only after a failure", async () => {
    listAdapters.mockRejectedValueOnce(new Error("shelf is closed"));
    const slots = ref([loader("3")]);
    const swap = useLoraSwap(slots);
    await flush();
    expect(swap.adaptersError.value).toContain("shelf is closed");

    slots.value = [loader("4")];
    await flush();
    expect(listAdapters).toHaveBeenCalledTimes(2);
    expect(swap.adaptersError.value).toBe("");

    slots.value = [loader("5")];
    await flush();
    expect(listAdapters).toHaveBeenCalledTimes(2);
  });
});

describe("what a run is sent", () => {
  it("sends nothing without a slot, and nothing without a choice", async () => {
    const slots = ref([]);
    const swap = useLoraSwap(slots);
    swap.adapterSha.value = SHA;
    expect(swap.body()).toEqual({});

    slots.value = [loader("3")];
    await flush();
    expect(swap.body()).toEqual({});
  });

  it("names no slot where there is only one", async () => {
    const swap = useLoraSwap(ref([loader("3")]));
    await flush();
    swap.adapterSha.value = SHA;
    expect(swap.body()).toEqual({ adapter_sha256: SHA });
  });

  it("names node and field where there are several, the first until chosen", async () => {
    const swap = useLoraSwap(ref([loader("7", "lora_name_1"), loader("7", "lora_name_2")]));
    await flush();
    swap.adapterSha.value = SHA;
    expect(swap.body()).toEqual({
      adapter_sha256: SHA,
      lora_node_id: "7",
      lora_field: "lora_name_1",
    });
    swap.chosenSlot.value = "lora_name_2@7";
    expect(swap.body()).toMatchObject({ lora_field: "lora_name_2" });
  });
});

describe("whose choice it is", () => {
  it("drops the choice when the graph changes", async () => {
    const slots = ref([loader("3")]);
    const swap = useLoraSwap(slots);
    await flush();
    swap.adapterSha.value = SHA;

    slots.value = [loader("12")];
    await flush();
    expect(swap.adapterSha.value).toBe("");
    expect(swap.chosenSlot.value).toBe("lora_name@12");
  });

  it("keeps it when the same slots are handed back as a new array", async () => {
    const slots = ref([loader("3")]);
    const swap = useLoraSwap(slots);
    await flush();
    swap.adapterSha.value = SHA;

    slots.value = [loader("3")];
    await flush();
    expect(swap.adapterSha.value).toBe(SHA);
  });
});

describe("adding a loader where there is none (#1376)", () => {
  const PLAN = {
    model: { node_id: "1", class_type: "UnetLoaderGGUF", output: 0 },
    clip: { node_id: "2", class_type: "DualCLIPLoader", output: 0 },
    rewires: [
      { node_id: "4", class_type: "CLIPTextEncode", field: "clip", type: "CLIP" },
      { node_id: "3", class_type: "ModelSamplingFlux", field: "model", type: "MODEL" },
    ],
  };
  const source = (key, result) => ref({ key, load: vi.fn().mockResolvedValue(result) });

  it("says where it goes and every input it takes over", () => {
    expect(insertionSummary(PLAN)).toBe(
      "A LoRA loader is added after #1 UnetLoaderGGUF and #2 DualCLIPLoader, " +
        "feeding #4 CLIPTextEncode (clip), #3 ModelSamplingFlux (model).",
    );
    expect(insertionSummary({ ...PLAN, clip: null, rewires: [] })).toContain(
      "loads the model only",
    );
    expect(insertionSummary(null)).toBe("");
  });

  it("asks only for a graph with no slot, and sends the opt-in once it can show it", async () => {
    const slots = ref([loader("3")]);
    const insertion = source("a.json", { plan: PLAN, reason: null });
    const swap = useLoraSwap(slots, insertion);
    await flush();
    expect(insertion.value.load).not.toHaveBeenCalled();

    slots.value = [];
    await flush();
    expect(insertion.value.load).toHaveBeenCalledTimes(1);
    expect(swap.canInsert.value).toBe(true);
    expect(swap.adapterOptions.value[0].label).toBe("No LoRA");
    swap.adapterSha.value = SHA;
    expect(swap.body()).toEqual({ adapter_sha256: SHA, insert_lora_loader: true });
  });

  it("sends nothing where no loader can be added", async () => {
    const swap = useLoraSwap(ref([]), source("a.json", { plan: null, reason: "Two models." }));
    await flush();
    swap.adapterSha.value = SHA;
    expect(swap.canInsert.value).toBe(false);
    expect(swap.insertion.value.reason).toBe("Two models.");
    expect(swap.body()).toEqual({});
    expect(listAdapters).not.toHaveBeenCalled();
  });

  it("drops the choice between two workflows that both have no slot", async () => {
    const insertion = source("a.json", { plan: PLAN, reason: null });
    const swap = useLoraSwap(ref([]), insertion);
    await flush();
    swap.adapterSha.value = SHA;

    insertion.value = { key: "b.json", load: vi.fn().mockResolvedValue({ plan: PLAN }) };
    await flush();
    expect(swap.adapterSha.value).toBe("");
  });

  it("ignores the answer for a graph no longer shown", async () => {
    let answerFirst;
    const insertion = ref({
      key: "a.json",
      load: () => new Promise((resolve) => (answerFirst = resolve)),
    });
    const swap = useLoraSwap(ref([]), insertion);
    await flush();
    insertion.value = source("b.json", { plan: null, reason: "Not this one." }).value;
    await flush();
    answerFirst({ plan: PLAN, reason: null });
    await flush();
    expect(swap.canInsert.value).toBe(false);
    expect(swap.insertion.value.reason).toBe("Not this one.");
  });

  it("says a failed check failed, rather than that nothing can be added", async () => {
    const insertion = ref({ key: "a.json", load: vi.fn().mockRejectedValue(new Error("403")) });
    const swap = useLoraSwap(ref([]), insertion);
    await flush();
    expect(swap.insertion.value.reason).toContain("The check failed");
  });
});

// The insertion read is owner-only, so a share-link session must not make it:
// the request can only 403, and an error where the honest answer is "this
// graph has no LoRA loader" reads as a fault rather than a fact. The one
// caller that guarded this was the lightbox's I2I menu, deleted in #1406, and
// neither surviving caller had copied the guard - so it lives here now.
describe("a read-only session", () => {
  it("never asks where a loader would go", async () => {
    const load = vi.fn().mockResolvedValue({ plan: { node_id: "9" } });
    isReadOnly.value = true;
    const { canInsert } = useLoraSwap(
      ref([]),
      ref({ key: "flux.json", load }),
    );
    await flush();
    expect(load).not.toHaveBeenCalled();
    expect(canInsert.value).toBe(false);
    // And nothing was fetched off the shelf on the back of it either.
    expect(listAdapters).not.toHaveBeenCalled();
  });

  it("still asks for an owner session, so the guard is the flag and not the wiring", async () => {
    const load = vi.fn().mockResolvedValue({ plan: { node_id: "9" } });
    useLoraSwap(ref([]), ref({ key: "flux.json", load }));
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
  });
});
