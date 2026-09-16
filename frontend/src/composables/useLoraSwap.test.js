// The LoRA swap rules shared by the run panel, "Generate variants" and "Edit
// with ComfyUI" (#1310). One implementation, so the rules are pinned here once;
// each surface's own spec covers only its wiring.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";

const listAdapters = vi.fn();
vi.mock("../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));

import { slotKey, slotLabel, useLoraSwap } from "./useLoraSwap";

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
