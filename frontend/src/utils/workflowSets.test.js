import { describe, expect, it } from "vitest";

import {
  FOLD_KEYS,
  comboCard,
  differences,
  foldCounts,
  foldDistance,
  foldSets,
  headModel,
  setCard,
  setName,
  worksWith,
} from "./workflowSets";

// The shelf's own kinds, not the design's words: `file_kind` is what the API
// serves and a fixture spelling it "Diffusion" would test nothing.
function model(id, name, kind = "checkpoint", extra = {}) {
  return { id, name, kind, filename: `${name}.safetensors`, ...extra };
}

function combination(
  key,
  models,
  { recipes = 1, pictures = 1, covers = [] } = {},
) {
  return {
    key,
    models,
    recipes,
    picture_count: pictures,
    covers: covers.map((id) => ({ picture_id: id, url: `/t/${id}.webp` })),
  };
}

const CKPT = model(1, "realvisXL_v5");
const VAE = model(2, "sdxl_vae", "vae");
const VAE_2 = model(3, "vae-ft-mse", "vae");
const CLIP = model(4, "clip_l", "text_encoder");
const LORA = model(5, "filmgrain_xl", "adapter");
const OTHER_CKPT = model(6, "juggernautXL_v9");

const BASE = combination("1,2,4", [CKPT, VAE, CLIP], {
  recipes: 5,
  pictures: 94,
  covers: [11, 12, 13],
});
const WITH_LORA = combination("1,2,4,5", [CKPT, VAE, CLIP, LORA], {
  recipes: 3,
  pictures: 44,
});
const SWAPPED_VAE = combination("1,3,4", [CKPT, VAE_2, CLIP], {
  recipes: 2,
  pictures: 28,
});
const UNRELATED = combination("2,4,6", [OTHER_CKPT, VAE, CLIP], {
  recipes: 4,
  pictures: 96,
});

describe("foldDistance", () => {
  it("counts a one-for-one swap as one step, not two", () => {
    // The symmetric difference is 2; the reader made one decision, and `1 file
    // apart` has to fold the pair that differs only in which VAE it loaded.
    expect(foldDistance(new Set([1, 2, 4]), new Set([1, 3, 4]))).toBe(1);
  });

  it("counts an added file as one step", () => {
    expect(foldDistance(new Set([1, 2, 4]), new Set([1, 2, 4, 5]))).toBe(1);
  });

  it("counts two unrelated changes as two", () => {
    expect(foldDistance(new Set([1, 2, 4]), new Set([1, 3, 7]))).toBe(2);
  });

  it("is symmetric", () => {
    const a = new Set([1, 2]);
    const b = new Set([1, 2, 3, 4]);
    expect(foldDistance(a, b)).toBe(foldDistance(b, a));
  });
});

describe("foldSets", () => {
  const all = [BASE, WITH_LORA, SWAPPED_VAE, UNRELATED];

  it("draws every combination when the reader turns folding off", () => {
    expect(foldSets(all, "none").map((s) => s.members.length)).toEqual([
      1, 1, 1, 1,
    ]);
  });

  it("folds the near-identical ones onto the most-used combination", () => {
    const stacks = foldSets(all, "one");
    // UNRELATED has the most pictures, so it seeds the first stack; BASE seeds
    // the second and takes the LoRA and the VAE swap with it.
    expect(stacks.map((stack) => stack.members[0].key)).toEqual([
      "2,4,6",
      "1,2,4",
    ]);
    const folded = stacks.find((stack) => stack.key === "1,2,4");
    expect(folded.members.map((m) => m.key)).toEqual([
      "1,2,4",
      "1,2,4,5",
      "1,3,4",
    ]);
  });

  it("never chains a combination onto a stack it is two files from", () => {
    // C is one file from B and two from A, and A seeds the stack. Single
    // linkage would put C under A's name; seeded folding must not.
    const a = combination("1,2,4", [CKPT, VAE, CLIP], { pictures: 90 });
    const b = combination("1,2,4,5", [CKPT, VAE, CLIP, LORA], { pictures: 50 });
    const c = combination(
      "1,2,4,5,7",
      [CKPT, VAE, CLIP, LORA, model(7, "x", "adapter")],
      {
        pictures: 10,
      },
    );

    const stacks = foldSets([a, b, c], "one");

    expect(stacks.map((stack) => stack.members.map((m) => m.key))).toEqual([
      ["1,2,4", "1,2,4,5"],
      ["1,2,4,5,7"],
    ]);
  });

  it("collapses everything under its checkpoint on the last setting", () => {
    const stacks = foldSets(all, "checkpoint");
    expect(stacks.map((stack) => stack.key)).toEqual(["model:6", "model:1"]);
    expect(stacks[1].members.map((m) => m.key)).toEqual([
      "1,2,4",
      "1,2,4,5",
      "1,3,4",
    ]);
  });

  it("keys the checkpoint fold on the id, so two files of one name stay apart", () => {
    const twin_a = combination("8,2", [model(8, "twin"), VAE], { pictures: 5 });
    const twin_b = combination("9,2", [model(9, "twin"), VAE], { pictures: 4 });

    expect(foldSets([twin_a, twin_b], "checkpoint")).toHaveLength(2);
  });

  it("handles an empty library without inventing a card", () => {
    for (const key of FOLD_KEYS) expect(foldSets([], key)).toEqual([]);
  });
});

describe("foldCounts", () => {
  it("states what each setting costs in cards", () => {
    expect(foldCounts([BASE, WITH_LORA, SWAPPED_VAE, UNRELATED])).toEqual({
      none: 4,
      one: 2,
      two: 2,
      checkpoint: 2,
    });
  });

  it("keeps By checkpoint only the coarsest setting, whatever the distance", () => {
    // The one ordering the fold really guarantees, and it holds because a fold
    // never swaps the head: every stack is inside one head's group, so nothing
    // finer than `checkpoint` can produce fewer cards than it does.
    const counts = foldCounts([BASE, WITH_LORA, SWAPPED_VAE, UNRELATED]);
    for (const key of ["none", "one", "two"]) {
      expect(counts[key]).toBeGreaterThanOrEqual(counts.checkpoint);
    }
    expect(counts.checkpoint).toBe(2); // two heads in the fixture
  });

  it("does NOT promise nested partitions, and this is the case that proves it", () => {
    // Seeding has a real cost and the docs state it rather than hiding it: the
    // seed set changes with the distance, so loosening can move a member onto a
    // different card instead of only merging cards. Pinned so the claim in
    // `foldSets`' docstring stays true of the code, and so that anybody who
    // later makes the settings genuinely nested finds this test and deletes it
    // deliberately.
    const head = model(20, "one_ckpt");
    const a = combination("20,21", [head, model(21, "a", "vae")], {
      pictures: 30,
    });
    const b = combination(
      "20,22,23",
      [head, model(22, "b", "vae"), model(23, "c", "adapter")],
      {
        pictures: 20,
      },
    );
    const c = combination(
      "20,22,23,24",
      [
        head,
        model(22, "b", "vae"),
        model(23, "c", "adapter"),
        model(24, "d", "adapter"),
      ],
      { pictures: 10 },
    );

    const at = (fold) =>
      foldSets([a, b, c], fold).map((stack) => stack.members.map((m) => m.key));

    // A seeds, B is 2 away, C is 1 from B: at "1 file apart" B seeds its own.
    expect(at("one")).toEqual([["20,21"], ["20,22,23", "20,22,23,24"]]);
    // At "2 files apart" A takes B, and C - 3 from A - is left on its own.
    expect(at("two")).toEqual([["20,21", "20,22,23"], ["20,22,23,24"]]);
  });
});

describe("differences", () => {
  it("names an added file", () => {
    expect(differences(BASE, WITH_LORA)).toEqual(["+ filmgrain_xl"]);
  });

  it("draws a same-kind exchange as one arrow rather than an add and a remove", () => {
    expect(differences(BASE, SWAPPED_VAE)).toEqual(["sdxl_vae → vae-ft-mse"]);
  });

  it("names a removed file", () => {
    expect(differences(WITH_LORA, BASE)).toEqual(["− filmgrain_xl"]);
  });

  it("does not let an added adapter take a removed encoder's place", () => {
    const without = combination("1,2", [CKPT, VAE]);
    const swapped = combination("1,2,5", [CKPT, VAE, LORA]);
    expect(
      differences(combination("1,2,4", [CKPT, VAE, CLIP]), swapped),
    ).toEqual(["+ filmgrain_xl", "− clip_l"]);
    expect(differences(without, without)).toEqual([]);
  });
});

describe("setCard", () => {
  it("names a set by the files that identify it and puts adapters on their own row", () => {
    const card = setCard({ key: "1,2,4,5", members: [WITH_LORA] });
    expect(card.name).toBe("realvisXL_v5 · sdxl_vae · clip_l");
    // The kind row says what SHAPE the set is; the name row above already
    // spells the first three files.
    expect(card.kinds).toEqual(["Checkpoint", "VAE", "Text encoder"]);
    expect(card.loras).toEqual(["filmgrain_xl"]);
  });

  it("says nothing to fold on a stack of one, so a missing deck is never ambiguous", () => {
    const card = setCard({ key: "1,2,4", members: [BASE] });
    expect(card.size).toBe(1);
    expect(card.differsBy).toEqual(["5 recipes", "nothing to fold"]);
  });

  it("carries the union of the differences and the summed counts on a stack", () => {
    const card = setCard({
      key: "1,2,4",
      members: [BASE, WITH_LORA, SWAPPED_VAE],
    });
    expect(card.size).toBe(3);
    expect(card.differsBy).toEqual(["+ filmgrain_xl", "sdxl_vae → vae-ft-mse"]);
    expect(card.pictures).toBe(94 + 44 + 28);
    expect(card.recipes).toBe(5 + 3 + 2);
    expect(card.covers).toEqual(["/t/11.webp", "/t/12.webp", "/t/13.webp"]);
  });

  it("keeps an adapter out of the name even when it would fit", () => {
    // The filter, exercised. With the adapter SECOND, `.slice(0, 3)` would
    // include it, so this is the fixture that can tell the filter from the cut:
    // a set that differs only by its LoRA must not be named after that LoRA,
    // because the chip row below already names it and the title has to identify
    // the set.
    const lora_first = combination("1,5,2,4", [CKPT, LORA, VAE, CLIP]);
    expect(setName(lora_first)).toBe("realvisXL_v5 · sdxl_vae · clip_l");
  });

  it("falls back to the members it has when a set is all adapters", () => {
    // Otherwise a set of adapters alone would be titled "Unnamed set".
    const adapters = combination("5", [LORA]);
    expect(setName(adapters)).toBe("filmgrain_xl");
  });
});

describe("headModel", () => {
  it("reads the server's own order rather than re-deciding the checkpoint", () => {
    // A Flux set carries no `checkpoint` row: the head is the diffusion file the
    // server put first, and nothing here may fall back to the VAE.
    const flux = combination("10,11", [
      model(10, "flux1-dev", "unknown"),
      model(11, "ae", "vae"),
    ]);
    expect(headModel(flux).name).toBe("flux1-dev");
    expect(headModel(combination("", []))).toBeNull();
  });
});

describe("comboCard", () => {
  it("lists every file with its kind, never truncated", () => {
    const card = comboCard(WITH_LORA, BASE, 1);
    expect(card.files.map((f) => [f.name, f.kindLabel])).toEqual([
      ["realvisXL_v5", "Checkpoint"],
      ["sdxl_vae", "VAE"],
      ["clip_l", "Text encoder"],
      ["filmgrain_xl", "LoRA"],
    ]);
  });

  it("names the seed by its files and the rest by what changed", () => {
    expect(comboCard(BASE, BASE, 0).name).toBe(
      "realvisXL_v5 · sdxl_vae · clip_l",
    );
    expect(comboCard(BASE, BASE, 0).note).toBe("most used · 5 recipes");
    expect(comboCard(WITH_LORA, BASE, 1).name).toBe("… + filmgrain_xl");
    expect(comboCard(WITH_LORA, BASE, 1).note).toBe(
      "1 file from the set above · 3 recipes",
    );
    expect(comboCard(SWAPPED_VAE, BASE, 1).note).toBe(
      "one file swapped · 2 recipes",
    );
  });
});

describe("worksWith", () => {
  const all = [BASE, WITH_LORA, SWAPPED_VAE, UNRELATED];

  it("ranks companions by the recipes backing each pairing", () => {
    const { companions, recipes } = worksWith(all, VAE.id);
    // sdxl_vae ran in BASE (5), WITH_LORA (3) and UNRELATED (4): 12 recipes.
    expect(recipes).toBe(12);
    expect(companions.map((c) => [c.name, c.recipes])).toEqual([
      ["clip_l", 12],
      ["realvisXL_v5", 8],
      ["juggernautXL_v9", 4],
      ["filmgrain_xl", 3],
    ]);
    // The bar is the ranking against the strongest pairing, not a share of all.
    expect(companions[0].share).toBe(100);
    expect(companions[1].share).toBe(67);
  });

  it("never lists the model itself and says which sets it is in", () => {
    const { companions, sets } = worksWith(all, CKPT.id);
    expect(companions.some((c) => c.id === CKPT.id)).toBe(false);
    expect(sets.map((s) => s.key)).toEqual(["1,2,4", "1,2,4,5", "1,3,4"]);
  });

  it("carries a companion's uncertainty forward from any one witness", () => {
    const sure = combination("1,2", [CKPT, VAE]);
    const unsure = combination("1,2", [CKPT, { ...VAE, ambiguous: true }], {
      recipes: 1,
      pictures: 1,
    });
    expect(worksWith([sure, unsure], CKPT.id).companions[0].ambiguous).toBe(
      true,
    );
  });

  it("answers for a model no recipe names without throwing", () => {
    expect(worksWith(all, 999)).toEqual({
      companions: [],
      recipes: 0,
      sets: [],
    });
  });
});
