// The rules the Workflows list is made of, tested away from the DOM.
//
// The cases here are the states the design says the list has to survive, not a
// sweep of the API: a workflow with no name (which in v1.11 is every workflow),
// one whose model names were deleted, and one whose pictures are all in the
// Scrapheap. Each is the ordinary path for some row in a real library.

import { describe, it, expect } from "vitest";
import {
  assetKind,
  baseModelName,
  filterWorkflows,
  groupWorkflows,
  groupedNumber,
  libraryState,
  namedAdapterCount,
  modelAssets,
  modelStem,
  modelSummary,
  sortWorkflows,
  UNSET_GROUP_KEY,
  workflowDescriptor,
} from "./workflowShelf";

const CHECKPOINT = { widget: "ckpt_name", name: "realvisxlv40.safetensors" };
const LORA = { widget: "lora_name", name: "add_detail_xl.safetensors" };
const LORA_2 = { widget: "lora_name", name: "film_grain.safetensors" };
const INPUT_PICTURE = { widget: "image", name: "reference.png" };

function row(overrides = {}) {
  return {
    topology_hash: "a".repeat(64),
    node_count: 47,
    variants: 1,
    pictures: 10,
    last_used: "2026-08-20T00:00:00Z",
    first_seen_at: "2026-08-01T00:00:00Z",
    assets: [CHECKPOINT, LORA],
    ...overrides,
  };
}

describe("assetKind", () => {
  it("reads the input a filename was given to, not the filename", () => {
    expect(assetKind(CHECKPOINT)).toBe("base");
    expect(assetKind(LORA)).toBe("lora");
  });

  it("calls a picture a picture however it was wired", () => {
    // The hasher records image inputs beside the models, and a LoadImage node's
    // widget is just called `image`. A graph's input picture is not something
    // the workflow is made of, so it must never reach the Models column.
    expect(assetKind(INPUT_PICTURE)).toBe("image");
    expect(assetKind({ widget: "lora_name", name: "odd.png" })).toBe("image");
  });
});

describe("modelAssets", () => {
  it("drops pictures, dedupes, and puts the base model first", () => {
    const assets = modelAssets([LORA, INPUT_PICTURE, CHECKPOINT, LORA]);
    expect(assets.map((a) => a.name)).toEqual([CHECKPOINT.name, LORA.name]);
  });

  it("is empty when the names were forgotten", () => {
    expect(modelAssets([])).toEqual([]);
    expect(modelAssets(undefined)).toEqual([]);
  });
});

describe("modelSummary", () => {
  it("shows two names and counts the rest", () => {
    expect(modelSummary([CHECKPOINT, LORA, LORA_2])).toBe(
      "realvisxlv40, add_detail_xl, +1",
    );
  });

  it("says nothing rather than nothing-with-punctuation when there are no names", () => {
    expect(modelSummary([])).toBe("");
  });
});

describe("workflowDescriptor", () => {
  it("names the graph when nothing else has", () => {
    expect(workflowDescriptor(row())).toBe("realvisxlv40, 1 LoRA, 47 nodes");
  });

  it("drops the clauses it cannot fill instead of printing empty ones", () => {
    // A recipe whose asset rows were deleted keeps its graph and loses only the
    // ability to say what it used. It must still be identifiable.
    expect(workflowDescriptor(row({ assets: [] }))).toBe("47 nodes");
    expect(
      workflowDescriptor(row({ assets: [CHECKPOINT], node_count: 1 })),
    ).toBe("realvisxlv40, 1 node");
  });

  it("pluralises adapters", () => {
    expect(
      workflowDescriptor(row({ assets: [CHECKPOINT, LORA, LORA_2] })),
    ).toBe("realvisxlv40, 2 LoRAs, 47 nodes");
  });
});

describe("baseModelName and namedAdapterCount", () => {
  it("read the graph rather than guessing from the filename", () => {
    expect(baseModelName([CHECKPOINT, LORA])).toBe("realvisxlv40");
    expect(namedAdapterCount([CHECKPOINT, LORA, LORA_2])).toBe(2);
  });

  it("answer null and zero when the names are gone", () => {
    expect(baseModelName([])).toBeNull();
    expect(namedAdapterCount([])).toBe(0);
  });

  it("names no base model when the variants disagree about which one", () => {
    // A row's assets are the SET across its variants, so a topology whose
    // variants each pick a different checkpoint names several. Heading the row
    // with one of them would claim this workflow uses that model.
    const other = { widget: "ckpt_name", name: "juggernautxl.safetensors" };
    expect(baseModelName([CHECKPOINT, other, LORA])).toBeNull();
  });
});

describe("workflowDescriptor and the 159-variant family", () => {
  it("counts adapter SLOTS, never the names its variants name between them", () => {
    // The measured worst case: 159 character LoRAs, one loaded per run. Built
    // from the names, the row's only identifying line reads "159 LoRAs" for a
    // graph that loads one.
    const manyNames = Array.from({ length: 159 }, (_, index) => ({
      widget: "lora_name",
      name: `character_${index}.safetensors`,
    }));
    const family = row({
      assets: [CHECKPOINT, ...manyNames],
      adapter_slots: 1,
      node_count: 71,
      variants: 159,
    });
    expect(workflowDescriptor(family)).toBe("realvisxlv40, 1 LoRA, 71 nodes");
    // And the union is still what the Models cell lists, capped.
    expect(modelSummary(family.assets)).toBe("realvisxlv40, character_0, +158");
  });

  it("falls back to counting for a variant, which has no slot count", () => {
    // Every name on a RECIPE is a file that run actually loaded, so counting
    // them is right there and only there.
    expect(
      workflowDescriptor({
        node_count: 47,
        assets: [CHECKPOINT, LORA, LORA_2],
      }),
    ).toBe("realvisxlv40, 2 LoRAs, 47 nodes");
  });
});

describe("modelStem", () => {
  it("keeps a dotted name whole and drops only the extension", () => {
    expect(modelStem("flux1-dev.v2.safetensors")).toBe("flux1-dev.v2");
    expect(modelStem("noextension")).toBe("noextension");
  });
});

describe("sortWorkflows", () => {
  const rows = [
    row({ topology_hash: "b".repeat(64), pictures: 5, node_count: 90 }),
    row({ topology_hash: "a".repeat(64), pictures: 50, node_count: 12 }),
    row({ topology_hash: "c".repeat(64), pictures: 5, node_count: 12 }),
  ];

  it("puts the most used first by default", () => {
    expect(sortWorkflows(rows, "used")[0].pictures).toBe(50);
  });

  it("breaks a tie on the hash so the list never reorders under itself", () => {
    // Two rows with five pictures each: a comparator without a stable tiebreak
    // is free to swap them between two reads of identical data, which reads to
    // the user as the list moving for no reason.
    const once = sortWorkflows(rows, "used").map((r) => r.topology_hash);
    const twice = sortWorkflows([...rows].reverse(), "used").map(
      (r) => r.topology_hash,
    );
    expect(once).toEqual(twice);
  });

  it("sorts by node count when asked, not by the count it displays", () => {
    expect(sortWorkflows(rows, "nodes")[0].node_count).toBe(90);
  });

  it("leaves the caller's array alone", () => {
    const original = [...rows];
    sortWorkflows(rows, "nodes");
    expect(rows).toEqual(original);
  });
});

describe("filterWorkflows", () => {
  const rows = [row({ pictures: 3 }), row({ pictures: 0 })];

  it("separates what is still in use from what only the hub remembers", () => {
    expect(filterWorkflows(rows, "in_use")).toHaveLength(1);
    expect(filterWorkflows(rows, "unused")).toHaveLength(1);
    expect(filterWorkflows(rows, "all")).toHaveLength(2);
  });
});

describe("groupWorkflows", () => {
  it("returns one unnamed band when nothing is grouped", () => {
    const bands = groupWorkflows([row(), row()], "none");
    expect(bands).toHaveLength(1);
    expect(bands[0].key).toBeNull();
  });

  it("sinks the band that could not be named, whatever the axis", () => {
    const bands = groupWorkflows(
      [row({ assets: [] }), row({ assets: [CHECKPOINT] })],
      "base_model",
    );
    expect(bands.map((band) => band.key)).toEqual([
      "realvisxlv40",
      UNSET_GROUP_KEY,
    ]);
    expect(bands[1].label).toBe("No model named");
  });

  it("orders the size bands by size, not alphabetically", () => {
    const bands = groupWorkflows(
      [
        row({ node_count: 90 }),
        row({ node_count: 5 }),
        row({ node_count: 30 }),
      ],
      "size",
    );
    expect(bands.map((band) => band.key)).toEqual(["small", "medium", "large"]);
  });
});

describe("libraryState", () => {
  it("tells the three empty states apart", () => {
    expect(libraryState({ pictures: 28172, scanned: 0 }, 0)).toBe("unscanned");
    expect(libraryState({ pictures: 28172, scanned: 8412 }, 0)).toBe(
      "scanning",
    );
    expect(libraryState({ pictures: 19943, scanned: 19943 }, 0)).toBe("none");
  });

  it("does not offer to scan a library with nothing in it", () => {
    expect(libraryState({ pictures: 0, scanned: 0 }, 0)).toBe("none");
  });

  it("is listed as soon as there is a row, mid-scan or not", () => {
    // The counts climb while the pass reads. Nothing here is wrong; it is just
    // not finished, and a half-read list must not read as an error.
    expect(libraryState({ pictures: 28172, scanned: 8412 }, 47)).toBe("listed");
  });
});

describe("groupedNumber", () => {
  it("groups the way every other count in the app does", () => {
    expect(groupedNumber(28172)).toBe("28 172");
    expect(groupedNumber(0)).toBe("0");
  });
});
