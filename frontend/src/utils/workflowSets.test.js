import { describe, expect, it } from "vitest";

import {
  fillFromPictures,
  fillFromSets,
  handMadeCard,
  handMadeName,
  headModel,
  kindCounts,
  setCard,
  setGroups,
  setName,
  setSlots,
  sharingLabel,
  slotSuggestions,
  worksWith,
} from "./workflowSets";

// The shelf's own kinds, not the design's words: `file_kind` is what the API
// serves and a fixture spelling it "Diffusion" would test nothing.
function model(id, name, kind = "checkpoint", extra = {}) {
  return {
    id,
    name,
    kind,
    filename: `${name}.safetensors`,
    file_size: 1000,
    ...extra,
  };
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
    // `{picture_id, version}`, which is what the route serves. NOT a URL: an
    // `<img src>` never reaches the Axios interceptor, so the card builds the src
    // through `pictureThumbnailUrl` and these stay records all the way.
    covers: covers.map((id) => ({ picture_id: id, version: `v${id}` })),
  };
}

const CKPT = model(1, "realvisXL_v5");
const VAE = model(2, "sdxl_vae", "vae");
const VAE_2 = model(3, "vae-ft-mse", "vae");
const CLIP = model(4, "clip_l", "text_encoder");
const LORA = model(5, "filmgrain_xl", "adapter");
const OTHER_CKPT = model(6, "juggernautXL_v9");

// Three recipes under `realvisXL_v5` and one under another checkpoint. The VAE and
// the encoder are in both groups, which is the overlap this whole axis exists for.
const BASE = combination("1,2,4", [CKPT, VAE, CLIP], {
  recipes: 5,
  pictures: 94,
  covers: [11, 12, 13],
});
const WITH_LORA = combination("1,2,4,5", [CKPT, VAE, CLIP, LORA], {
  recipes: 3,
  pictures: 44,
  covers: [12, 21],
});
const SWAPPED_VAE = combination("1,3,4", [CKPT, VAE_2, CLIP], {
  recipes: 2,
  pictures: 28,
});
const UNRELATED = combination("2,4,6", [OTHER_CKPT, VAE, CLIP], {
  recipes: 4,
  pictures: 96,
});
const ALL = [BASE, WITH_LORA, SWAPPED_VAE, UNRELATED];

describe("setGroups", () => {
  it("draws one group per base model, strongest evidence first", () => {
    const groups = setGroups(ALL);

    // By the GROUP's own total, not by its strongest single recipe: juggernaut
    // has the biggest one combination (96 pictures) and realvis has the biggest
    // set (94 + 44 + 28 = 166), and it is the set the card stands for.
    expect(groups.map((g) => g.key)).toEqual(["model:1", "model:6"]);
    expect(groups.map((g) => g.head.name)).toEqual([
      "realvisXL_v5",
      "juggernautXL_v9",
    ]);
    // Summed across the group's combinations, not taken from one of them.
    const realvis = groups[0];
    expect(realvis.recipes).toBe(5 + 3 + 2);
    expect(realvis.pictures).toBe(94 + 44 + 28);
  });

  it("holds the union of everything the base model has run with", () => {
    const realvis = setGroups(ALL).find((g) => g.key === "model:1");

    // Both VAEs, though they never ran together: that is what a union is, and the
    // tray says so in words rather than the data pretending otherwise.
    expect(realvis.models.map((m) => m.name)).toEqual([
      "realvisXL_v5",
      "sdxl_vae",
      "vae-ft-mse",
      "clip_l",
      "filmgrain_xl",
    ]);
  });

  it("gives each member its OWN evidence, not the group's total", () => {
    const realvis = setGroups(ALL).find((g) => g.key === "model:1");
    const by = Object.fromEntries(realvis.models.map((m) => [m.name, m]));

    // The LoRA ran in one of the three combinations, so it says so - it does not
    // inherit the group's 10 recipes or 166 pictures.
    expect([by.filmgrain_xl.recipes, by.filmgrain_xl.pictures]).toEqual([
      3, 44,
    ]);
    // The shared VAE ran in two of them.
    expect([by.sdxl_vae.recipes, by.sdxl_vae.pictures]).toEqual([8, 138]);
    // The head is in all three.
    expect(by.realvisXL_v5.recipes).toBe(10);
  });

  it("counts how many OTHER groups each member is in", () => {
    const realvis = setGroups(ALL).find((g) => g.key === "model:1");
    const by = Object.fromEntries(realvis.models.map((m) => [m.name, m]));

    // `sdxl_vae` and `clip_l` serve both checkpoints; the LoRA serves one.
    expect(by.sdxl_vae.otherSets).toBe(1);
    expect(by.clip_l.otherSets).toBe(1);
    expect(by.filmgrain_xl.otherSets).toBe(0);
    expect(by["vae-ft-mse"].otherSets).toBe(0);
  });

  it("keys on the head's id, so two files of one name stay apart", () => {
    const twinA = combination("8,2", [model(8, "twin"), VAE], { pictures: 5 });
    const twinB = combination("9,2", [model(9, "twin"), VAE], { pictures: 4 });

    expect(setGroups([twinA, twinB])).toHaveLength(2);
  });

  it("carries a member's uncertainty forward from any one witness", () => {
    const sure = combination("1,2", [CKPT, VAE], { pictures: 9 });
    const unsure = combination("1,2,4", [
      CKPT,
      { ...VAE, ambiguous: true },
      CLIP,
    ]);

    const group = setGroups([sure, unsure])[0];
    const vae = group.models.find((m) => m.id === VAE.id);

    // One witness that could not pin the file down is enough; a cleaner second
    // witness does not unmake the first.
    expect(vae.ambiguous).toBe(true);
    expect(group.models.find((m) => m.id === CKPT.id).ambiguous).toBe(false);
  });

  it("pools the covers across a group's recipes, deduplicated, best first", () => {
    const realvis = setGroups(ALL).find((g) => g.key === "model:1");

    // 11, 12, 13 from BASE and 12, 21 from WITH_LORA: 12 once, capped at three.
    expect(realvis.covers.map((c) => c.picture_id)).toEqual([11, 12, 13]);
    expect(realvis.covers[0]).toEqual({ picture_id: 11, version: "v11" });
  });

  it("reads the server's own kind order rather than re-deciding it", () => {
    // A Flux group carries no `checkpoint` row: the head is the diffusion file the
    // server put first, and nothing here may fall back to the VAE.
    const flux = combination("10,11", [
      model(10, "flux1-dev", "unknown"),
      model(11, "ae", "vae"),
    ]);
    expect(setGroups([flux])[0].head.name).toBe("flux1-dev");
    expect(headModel(combination("", []))).toBeNull();
  });

  it("handles an empty library without inventing a group", () => {
    expect(setGroups([])).toEqual([]);
    expect(setGroups(undefined)).toEqual([]);
  });
});

describe("setCard", () => {
  const realvis = () => setGroups(ALL).find((g) => g.key === "model:1");

  it("is named by the base model, with its kind beside it", () => {
    const card = setCard(realvis());
    expect(card.name).toBe("realvisXL_v5");
    expect(card.kindLabel).toBe("Checkpoint");
  });

  it("tallies the rest by kind, which is what the grid is scanned for", () => {
    // The shape of the set - two VAEs, one encoder, one LoRA - without opening
    // anything. The head is not in its own tally.
    expect(setCard(realvis()).kinds).toEqual([
      "VAE 2",
      "Text encoder 1",
      "LoRA 1",
    ]);
  });

  it("states the facts the card's last row carries", () => {
    expect(setCard(realvis()).facts).toEqual([
      "5 models",
      "10 recipes",
      "166 pictures",
    ]);
  });

  it("says so when nothing else has ever run with it", () => {
    const alone = combination("1", [CKPT], { recipes: 2, pictures: 3 });
    const card = setCard(setGroups([alone])[0]);
    expect(card.kinds).toEqual([]);
    expect(card.facts).toEqual(["1 model", "2 recipes", "3 pictures"]);
  });
});

describe("kindCounts", () => {
  it("writes a count of one out, so a tally does not read as a label", () => {
    expect(kindCounts([VAE])).toEqual(["VAE 1"]);
  });

  it("keeps the members' own order rather than sorting alphabetically", () => {
    expect(kindCounts([VAE, CLIP, LORA])).toEqual([
      "VAE 1",
      "Text encoder 1",
      "LoRA 1",
    ]);
  });
});

describe("sharingLabel", () => {
  it("names the count, because a list of seventeen is not readable", () => {
    expect(sharingLabel(0)).toBe("Only in this set");
    expect(sharingLabel(1)).toBe("Also in 1 other set");
    expect(sharingLabel(17)).toBe("Also in 17 other sets");
  });
});

describe("worksWith", () => {
  it("ranks companions by the recipes backing each pairing", () => {
    const { companions, recipes } = worksWith(ALL, VAE.id);
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

  it("reads the COMBINATIONS, so a union cannot invent a pairing", () => {
    // The two VAEs are in one group and have never run together. Answering from
    // the group's union would report each as the other's companion; answering from
    // the combinations cannot. This is the whole reason the payload is not folded
    // into unions on the way in.
    expect(worksWith(ALL, VAE.id).companions.map((c) => c.name)).not.toContain(
      "vae-ft-mse",
    );
    const group = setGroups(ALL).find((g) => g.key === "model:1");
    expect(group.models.map((m) => m.name)).toContain("vae-ft-mse");
  });

  it("never lists the model itself and says which sets it is in", () => {
    const { companions, sets } = worksWith(ALL, CKPT.id);
    expect(companions.some((c) => c.id === CKPT.id)).toBe(false);
    expect(sets.map((s) => s.key)).toEqual(["1,2,4", "1,2,4,5", "1,3,4"]);
  });

  it("carries a companion's uncertainty forward from any one witness", () => {
    const sure = combination("1,2", [CKPT, VAE]);
    const unsure = combination("1,2", [CKPT, { ...VAE, ambiguous: true }]);
    expect(worksWith([sure, unsure], CKPT.id).companions[0].ambiguous).toBe(
      true,
    );
  });

  it("answers for a model no recipe names without throwing", () => {
    expect(worksWith(ALL, 999)).toEqual({
      companions: [],
      recipes: 0,
      sets: [],
    });
  });
});

describe("setName", () => {
  it("names a combination by the files that identify it", () => {
    expect(setName(WITH_LORA)).toBe("realvisXL_v5 · sdxl_vae · clip_l");
  });

  it("keeps an adapter out of the name even when it would fit", () => {
    // With the adapter SECOND, `.slice(0, 3)` would include it, so this is the
    // fixture that can tell the filter from the cut.
    const loraFirst = combination("1,5,2,4", [CKPT, LORA, VAE, CLIP]);
    expect(setName(loraFirst)).toBe("realvisXL_v5 · sdxl_vae · clip_l");
  });

  it("falls back to the members it has when a set is all adapters", () => {
    expect(setName(combination("5", [LORA]))).toBe("filmgrain_xl");
  });
});

describe("hand-made sets (#1520)", () => {
  const shelfRow = (id, name, fileKind, base = "SDXL") => ({
    id,
    sha256: `h${id}`,
    file_kind: fileKind,
    display_name: name,
    filename: `${name}.safetensors`,
    base_model: base,
  });
  const slotMember = (id, name, slot, extra = {}) => ({
    sha256: `h${id}`,
    slot,
    label: name,
    on_shelf: true,
    id,
    name,
    base_model: "SDXL",
    ...extra,
  });

  const rows = [
    shelfRow(1, "RealVis XL", "checkpoint"),
    shelfRow(2, "Film Grain", "adapter"),
    shelfRow(3, "Soft Light", "adapter"),
    shelfRow(4, "Ink Wash", "adapter"),
    shelfRow(5, "Flux LoRA", "adapter", "Flux"),
    shelfRow(6, "Tagger", "engine"),
  ];
  const mine = { id: 1, members: [slotMember(1, "RealVis XL", "checkpoint")] };
  const other = {
    id: 2,
    members: [
      slotMember(9, "Juggernaut", "checkpoint"),
      slotMember(3, "Soft Light", "lora"),
    ],
  };
  const combinations = [
    {
      key: "1,4",
      recipes: 2,
      picture_count: 2,
      models: [
        { id: 1, name: "RealVis XL", kind: "checkpoint" },
        { id: 4, name: "Ink Wash", kind: "adapter" },
      ],
    },
  ];

  it("ranks your sets, then recipes with the checkpoint, then the base model, then all", () => {
    const sections = slotSuggestions({
      set: mine,
      slotId: "lora",
      rows,
      sets: [mine, other],
      combinations,
    });
    const names = Object.fromEntries(
      sections.map((s) => [s.id, s.items.map((r) => r.display_name)]),
    );
    expect(names).toEqual({
      sets: ["Soft Light"],
      checkpoint: ["Ink Wash"],
      base: ["Film Grain"],
      all: ["Flux LoRA"],
    });
  });

  it("is one unfiltered list until a checkpoint is chosen, and never offers an engine", () => {
    const sections = slotSuggestions({
      set: { id: 3, members: [] },
      slotId: "other",
      rows,
      sets: [],
      combinations,
    });
    expect(sections).toHaveLength(1);
    const names = sections[0].items.map((r) => r.display_name);
    expect(names).not.toContain("Tagger");
    expect(names).toContain("RealVis XL");
  });

  it("filters by the typed text and leaves out what the set already holds", () => {
    const held = {
      ...mine,
      members: [...mine.members, slotMember(2, "Film Grain", "lora")],
    };
    const sections = slotSuggestions({
      set: held,
      slotId: "lora",
      rows,
      sets: [held],
      combinations,
      query: "  in",
    });
    const all = sections.flatMap((s) => s.items.map((r) => r.display_name));
    expect(all).toEqual(["Ink Wash"]);
  });

  it("fills from pictures with what ran beside the checkpoint, in its own slot", () => {
    expect(fillFromPictures(mine, combinations, rows)).toMatchObject([
      { id: 4, slot: "lora" },
    ]);
    expect(
      fillFromPictures({ id: 5, members: [] }, combinations, rows),
    ).toEqual([]);
  });

  it("fills from a set with other sets' members, never their checkpoint", () => {
    expect(fillFromSets(mine, [mine, other])).toMatchObject([
      { id: 3, slot: "lora", from: ["Juggernaut"] },
    ]);
  });

  it("lists every slot with its ＋, and drops Checkpoint's once it holds one", () => {
    const keys = setSlots(mine).map(({ slot, items }) => [
      slot.id,
      items.map((i) => i.key),
    ]);
    expect(keys).toEqual([
      ["checkpoint", ["m:h1"]],
      ["text_encoder", ["add:text_encoder"]],
      ["vae", ["add:vae"]],
      ["lora", ["add:lora"]],
      ["other", ["add:other"]],
    ]);
  });

  it("names a set by its own name, its checkpoint's, or Untitled set", () => {
    expect(handMadeName({ name: "Kit", members: mine.members })).toBe("Kit");
    expect(handMadeName(mine)).toBe("RealVis XL");
    expect(handMadeName({ members: [] })).toBe("Untitled set");
    expect(handMadeCard({ id: 4, members: [] })).toMatchObject({
      handMade: true,
      incomplete: true,
      name: "Untitled set",
    });
  });
});
