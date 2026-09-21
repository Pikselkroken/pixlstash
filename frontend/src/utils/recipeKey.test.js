// The key two looks are compared on (v1.12 F6).
//
// This has to agree with `saved_recipe_service.prompt_key` / `lora_key`, which
// is what the server groups credit by. When it does not, the lightbox says
// "Matches your saved recipe X" over a recipe the server credits 0 pictures —
// or, the way it actually went wrong here, the saved recipe never matches the
// picture it was saved from and the same look can be kept over and over.

import { describe, it, expect } from "vitest";

import {
  keepsTheSameLook,
  loraKey,
  promptKey,
  wouldDuplicate,
} from "./recipeKey";

describe("promptKey", () => {
  it("strips, and treats absent as the empty prompt", () => {
    expect(promptKey("  a castle on a hill\n")).toBe("a castle on a hill");
    expect(promptKey(null)).toBe("");
    expect(promptKey(undefined)).toBe("");
  });

  it("keeps case and inner spacing, which are looks told apart on purpose", () => {
    expect(promptKey("A Castle")).not.toBe(promptKey("a castle"));
    expect(promptKey("a  castle")).not.toBe(promptKey("a castle"));
  });
});

describe("loraKey", () => {
  it("reads file names and recipe rows to the same key", () => {
    expect(loraKey(["Styles/Mira_v2.SAFETENSORS"])).toBe(
      loraKey([{ filename: "mira_v2.safetensors" }]),
    );
  });

  it("ignores order, because neither side's order means anything", () => {
    expect(loraKey(["b.safetensors", "a.safetensors"])).toBe(
      loraKey(["a.safetensors", "b.safetensors"]),
    );
  });

  it("keeps duplicates, because a stacked LoRA is a different look", () => {
    expect(loraKey(["a.safetensors", "a.safetensors"])).not.toBe(
      loraKey(["a.safetensors"]),
    );
  });

  it("drops an entry that names no file rather than comparing it", () => {
    expect(loraKey([null, "", { strength: 1 }, "a.safetensors"])).toBe(
      loraKey(["a.safetensors"]),
    );
    expect(loraKey(null)).toBe("");
  });
});

describe("keepsTheSameLook", () => {
  const look = { prompt: "a castle", loras: ["style.safetensors"] };

  it("matches a recipe holding the same prompt and LoRAs", () => {
    expect(
      keepsTheSameLook(
        { prompt: " a castle ", loras: [{ filename: "STYLE.safetensors" }] },
        look,
      ),
    ).toBe(true);
  });

  it("refuses when either half differs", () => {
    expect(keepsTheSameLook({ prompt: "a castle", loras: [] }, look)).toBe(
      false,
    );
    expect(
      keepsTheSameLook({ prompt: "a keep", loras: look.loras }, look),
    ).toBe(false);
  });

  it("never matches a look that is neither a prompt nor a LoRA", () => {
    // The key of an empty recipe is also the key of every picture PixlStash
    // has not read the metadata out of yet, which the server refuses to
    // credit. One such recipe would otherwise claim all of them.
    expect(keepsTheSameLook({ prompt: "", loras: [] }, { prompt: "", loras: [] })).toBe(
      false,
    );
    expect(keepsTheSameLook({ prompt: "", loras: [] }, {})).toBe(false);
  });
});

// A recipe is more than its look, and the Run popup is a form that can edit
// every part of it that the look's key leaves out (#1480). Keyed on the look
// alone, an inert "Saved" refuses to keep a variation — which is not a
// duplicate, and there is then no way to save it from that surface at all.
describe("wouldDuplicate", () => {
  /** A kept recipe, and the save that would write exactly it again. */
  const KEPT = {
    prompt: "a castle on a hill",
    negative: "blurry",
    loras: [{ filename: "Styles/Mira_v2.safetensors", strength: 0.85 }],
    overrides: { "KSampler/steps": 12 },
  };
  const SAME = {
    prompt: "  a castle on a hill  ",
    negative: " blurry ",
    loras: [{ filename: "mira_v2.safetensors", strength: 0.85 }],
    overrides: { "KSampler/steps": 12 },
  };

  it("is true for the save that would write this very row again", () => {
    expect(wouldDuplicate(KEPT, SAME)).toBe(true);
  });

  it("is false once any part the save writes differs", () => {
    // Each of these is a part of the recipe that `keepsTheSameLook` ignores
    // on purpose, because none of them is part of what credit groups by.
    expect(wouldDuplicate(KEPT, { ...SAME, overrides: {} })).toBe(false);
    expect(
      wouldDuplicate(KEPT, { ...SAME, overrides: { "KSampler/steps": 30 } }),
    ).toBe(false);
    expect(wouldDuplicate(KEPT, { ...SAME, negative: "" })).toBe(false);
    expect(
      wouldDuplicate(KEPT, {
        ...SAME,
        loras: [{ filename: "mira_v2.safetensors", strength: 0.4 }],
      }),
    ).toBe(false);
    // And the look itself still decides first.
    expect(wouldDuplicate(KEPT, { ...SAME, prompt: "a castle at dusk" })).toBe(
      false,
    );
  });

  it("never matches a recipe that keeps a seed", () => {
    // A save leaves the seed off unless the owner ticks the row, so the two
    // are different recipes whatever else they share.
    expect(wouldDuplicate({ ...KEPT, seed: "7", keep_seed: true }, SAME)).toBe(
      false,
    );
  });

  it("inherits the look's refusal to match nothing", () => {
    expect(wouldDuplicate({ prompt: "", loras: [] }, { prompt: "", loras: [] })).toBe(
      false,
    );
  });
});
