// Which workflows fit a person's or a set's LoRA (Create with LoRA…).

import { describe, it, expect } from "vitest";

import { fitWorkflows, nameKey, pairedCheckpoints } from "./loraWorkflows";

const LORA = {
  sha256: "a".repeat(64),
  filename: "example-subject-v2.safetensors",
  base_model_family: "krea2",
};
const CKPT = "c".repeat(64);

function card(
  id,
  {
    family = "krea2",
    sha256 = null,
    used = [],
    type = "txt2img",
    loras = 0,
  } = {},
) {
  return {
    id,
    name: id,
    type,
    models: [
      { kind: "unet", name: `${id}-model`, base_model_family: family, sha256 },
    ],
    loras: Array.from({ length: loras }, () => ({ kind: "lora", name: null })),
    recipe_values: { loras: used.map((name) => ({ name, pictures: 1 })) },
  };
}

const ids = (entries) => entries.map((entry) => entry.card.id);

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
  it("narrows to the LoRA's family, and keeps the unknowns apart", () => {
    const { match, unknown, clash, needsPicture } = fitWorkflows(
      [
        card("krea"),
        card("sdxl", { family: "sdxl" }),
        card("unread", { family: null }),
        card("edit", { type: "img2img" }),
        card("untyped", { type: null }),
        card("wan", { type: "video" }),
        card("wan-sdxl", { type: "video", family: "sdxl" }),
      ],
      LORA,
      [],
    );
    expect(ids(match)).toEqual(["krea", "untyped", "wan"]);
    expect(ids(unknown)).toEqual(["unread"]);
    expect(ids(clash)).toEqual(["sdxl", "wan-sdxl"]);
    expect(ids(needsPicture)).toEqual(["edit"]);
  });

  it("offers a workflow with no LoRA loader: the run adds one", () => {
    const { match } = fitWorkflows([card("bare", { loras: 0 })], LORA, []);
    expect(ids(match)).toEqual(["bare"]);
  });

  it("narrows nothing for a LoRA whose base model is not known", () => {
    const { match, unknown, clash } = fitWorkflows(
      [card("krea"), card("sdxl", { family: "sdxl" })],
      { ...LORA, base_model_family: null },
      [],
    );
    expect(match).toEqual([]);
    expect(clash).toEqual([]);
    expect(ids(unknown)).toEqual(["krea", "sdxl"]);
  });

  it("ranks a hand-made pairing first, then prior use, then server order", () => {
    const handMade = [
      {
        members: [
          { slot: "lora", sha256: LORA.sha256 },
          { slot: "checkpoint", sha256: CKPT },
        ],
      },
    ];
    const { match } = fitWorkflows(
      [
        card("first"),
        card("used", { used: ["example-subject-v2"] }),
        card("paired", { sha256: CKPT }),
      ],
      LORA,
      handMade,
    );
    expect(ids(match)).toEqual(["paired", "used", "first"]);
    expect(match[0].paired).toBe(true);
    expect(match[1].usedBefore).toBe(true);
  });
});

describe("pairedCheckpoints", () => {
  it("ignores sets that do not hold the LoRA", () => {
    const sets = [
      { members: [{ slot: "checkpoint", sha256: CKPT }] },
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
