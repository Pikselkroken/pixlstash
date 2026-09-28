// Which workflows fit a person's or a set's LoRA (Create with LoRA…).

import { describe, it, expect } from "vitest";

import {
  defaultLoader,
  familiesClash,
  fitWorkflows,
  loraPlacement,
  nameKey,
  pairedCheckpoints,
  replacedBy,
} from "./loraWorkflows";

const LORA = {
  sha256: "a".repeat(64),
  filename: "example-subject-v2.safetensors",
  family: "sdxl",
};
const SDXL_CKPT = "c".repeat(64);

function card(
  id,
  {
    family = "sdxl",
    sha256 = null,
    loras = 1,
    used = [],
    type = "txt2img",
  } = {},
) {
  return {
    id,
    name: id,
    type,
    models: [{ kind: "checkpoint", name: `${id}-ckpt`, family, sha256 }],
    loras: Array.from({ length: loras }, () => ({ kind: "lora", name: null })),
    recipe_values: { loras: used.map((name) => ({ name, pictures: 1 })) },
  };
}

describe("familiesClash", () => {
  it("only clashes when both families are known and differ", () => {
    expect(familiesClash("sdxl", "flux1")).toBe(true);
    expect(familiesClash("sdxl", "sdxl")).toBe(false);
    expect(familiesClash(null, "flux1")).toBe(false);
    expect(familiesClash("sdxl", null)).toBe(false);
  });
});

describe("nameKey", () => {
  it("folds folders, extension and case", () => {
    expect(nameKey("sd15/Example-Subject-V2.safetensors")).toBe(
      "example-subject-v2",
    );
    expect(nameKey("example-subject-v2")).toBe("example-subject-v2");
    expect(nameKey(null)).toBe("");
  });
});

describe("fitWorkflows", () => {
  it("sorts cards into ready, clash and no loader", () => {
    const { ready, clash, noLoader, needsPicture } = fitWorkflows(
      [
        card("ok"),
        card("flux", { family: "flux1" }),
        card("bare", { loras: 0 }),
        card("unknown", { family: null }),
        card("i2i", { type: "img2img" }),
        card("untyped", { type: null }),
      ],
      LORA,
      [],
    );
    expect(ready.map((e) => e.card.id)).toEqual(["ok", "unknown", "untyped"]);
    expect(clash.map((e) => e.card.id)).toEqual(["flux"]);
    expect(noLoader.map((e) => e.card.id)).toEqual(["bare"]);
    expect(needsPicture.map((e) => e.card.id)).toEqual(["i2i"]);
  });

  it("ranks a hand-made pairing first, then prior use, then server order", () => {
    const handMade = [
      {
        members: [
          { slot: "lora", sha256: LORA.sha256 },
          { slot: "checkpoint", sha256: SDXL_CKPT },
        ],
      },
    ];
    const { ready } = fitWorkflows(
      [
        card("first"),
        card("used", { used: ["example-subject-v2"] }),
        card("paired", { sha256: SDXL_CKPT }),
      ],
      LORA,
      handMade,
    );
    expect(ready.map((e) => e.card.id)).toEqual(["paired", "used", "first"]);
    expect(ready[0].paired).toBe(true);
    expect(ready[1].usedBefore).toBe(true);
  });
});

describe("pairedCheckpoints", () => {
  it("ignores sets that do not hold the LoRA", () => {
    const sets = [
      { members: [{ slot: "checkpoint", sha256: SDXL_CKPT }] },
      {
        members: [
          { slot: "lora", sha256: LORA.sha256 },
          { slot: "checkpoint", sha256: "d".repeat(64) },
        ],
      },
    ];
    expect([...pairedCheckpoints(sets, LORA.sha256)]).toEqual(["d".repeat(64)]);
  });
});

describe("placing the LoRA", () => {
  const loader = (node_id, filename, extra = {}) => ({
    node_id,
    field: "lora_name",
    filename,
    name: nameKey(filename),
    ...extra,
  });

  it("prefers the loader already loading the same bytes", () => {
    const loaders = [
      loader("1", "other.safetensors"),
      loader("2", "x", { sha256: LORA.sha256 }),
    ];
    expect(defaultLoader(loaders, LORA)).toBe(loaders[1]);
    expect(replacedBy(loaders[1], LORA)).toBeNull();
  });

  it("then a loader naming the same file", () => {
    const loaders = [
      loader("1", "other.safetensors"),
      loader("2", "sd15/example-subject-v2.safetensors"),
    ];
    expect(defaultLoader(loaders, LORA).node_id).toBe("2");
  });

  it("then an empty loader, which replaces nothing", () => {
    const loaders = [loader("1", "other.safetensors"), loader("2", "None")];
    expect(defaultLoader(loaders, LORA)).toBe(loaders[1]);
    expect(replacedBy(loaders[1], LORA)).toBeNull();
  });

  it("else the last loader, and names what it replaces", () => {
    const loaders = [
      loader("1", "first.safetensors"),
      loader("9", "detail.safetensors"),
    ];
    expect(defaultLoader(loaders, LORA)).toBe(loaders[1]);
    expect(replacedBy(loaders[1], LORA)).toBe("detail");
  });

  it("uses the trunk when it has loaders", () => {
    const chain = {
      loaders: [loader("1", "a.safetensors")],
      lanes: [{ loaders: [loader("9", "b.safetensors")] }],
    };
    expect(loraPlacement(chain)).toEqual({
      mode: "trunk",
      loaders: chain.loaders,
    });
  });

  it("uses every pass of a forked graph with no shared loader", () => {
    const high = [loader("5", "None")];
    const low = [loader("8", "None")];
    const chain = {
      loaders: [],
      lanes: [{ loaders: high }, { loaders: [] }, { loaders: low }],
    };
    expect(loraPlacement(chain)).toEqual({
      mode: "lanes",
      loaders: [high, low],
    });
  });

  it("is null for a chain with no loader", () => {
    expect(loraPlacement({ loaders: [], lanes: [] })).toBeNull();
  });
});
