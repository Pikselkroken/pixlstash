// The arithmetic behind the "+N" clip and the card's accessible name. The
// component suites cannot lay anything out, so the fit is pinned here on
// numbers.

import { describe, expect, it } from "vitest";

import {
  cardAccessibleName,
  factChips,
  fitChipCount,
  loraChips,
  ratingLabel,
  checkpointModel,
  checkpointUnread,
  lorasUnread,
  modelDisplayName,
} from "./workflowCard";

describe("fitChipCount", () => {
  it("shows every chip when they all fit, with no room kept for +N", () => {
    // 3 × 40 + 2 gaps of 4 = 128. Reserving the +N width here would clip a row
    // that fits exactly.
    expect(fitChipCount([40, 40, 40], 128, 4, 24)).toBe(3);
  });

  it("keeps room for +N once anything is clipped", () => {
    // Two chips fit alone (84), but not with the +N chip after them (84+4+24).
    expect(fitChipCount([40, 40, 40], 110, 4, 24)).toBe(1);
    expect(fitChipCount([40, 40, 40], 112, 4, 24)).toBe(2);
  });

  it("never reduces a row to a bare +N", () => {
    expect(fitChipCount([300, 40], 100, 4, 24)).toBe(1);
  });

  it("is zero for an empty row", () => {
    expect(fitChipCount([], 100, 4, 24)).toBe(0);
  });
});

const STACK = {
  name: "Cinematic portrait",
  models: [
    { name: "sdxl_vae", kind: "vae" },
    { name: "realvisXL_v5", kind: "checkpoint" },
  ],
  loras: [
    { name: "lightning-8step", mark: "structural" },
    { name: "", mark: "recipe" },
  ],
  differs_by: ["+ face detailer", "+ upscale 2×"],
  type: "txt2img",
  picture_count: 184,
  rating: 4.85,
  stack_size: 6,
};

describe("card chips", () => {
  it("draws a recipe slot dashed and a workflow LoRA as a plain chip", () => {
    // The mark is B1's own word, so nothing inverts on the way in.
    expect(loraChips(STACK).map((c) => [c.label, !!c.dashed])).toEqual([
      ["lightning-8step", false],
      ["recipe LoRA", true],
    ]);
  });

  it("names the checkpoint slot, whatever order the models arrive in", () => {
    expect(checkpointModel(STACK).name).toBe("realvisXL_v5");
    // No checkpoint slot (a unet graph): the first model still names the row.
    expect(
      checkpointModel({ models: [{ name: "flux1-dev", kind: "unet" }] }).name,
    ).toBe("flux1-dev");
    expect(checkpointModel({})).toBeNull();
  });

  it("calls a model what the SHELF calls it, and the file only otherwise", () => {
    // The card's `name` row was built from `title` server-side, so a chip
    // showing `name` beside it describes one model twice - the pair that
    // drifted in #1416.
    expect(
      modelDisplayName({ name: "realvisxl.safetensors", title: "Krea 2" }),
    ).toBe("Krea 2");
    // Null title is the ORDINARY case (the shelf has not scanned the file),
    // so it falls through to the filename rather than blanking the chip.
    expect(
      modelDisplayName({ name: "realvisxl.safetensors", title: null }),
    ).toBe("realvisxl.safetensors");
    // A payload that predates `title` at all behaves the same way.
    expect(modelDisplayName({ name: "flux1-dev" })).toBe("flux1-dev");
    expect(modelDisplayName({ name: null, title: null })).toBeNull();
    expect(modelDisplayName(null)).toBeNull();
  });

  it("speaks the shelf's name in the accessible name too", () => {
    // A screen reader hearing "Krea 2: Text to Image, checkpoint
    // realvisxl.safetensors" is the same contradiction the eye sees.
    const card = {
      name: "Krea 2: Text to Image",
      models: [
        {
          name: "realvisxl.safetensors",
          title: "Krea 2",
          kind: "checkpoint",
        },
      ],
      picture_count: 1,
    };
    const spoken = cardAccessibleName(card);
    expect(spoken).toContain("checkpoint Krea 2");
    expect(spoken).not.toContain("realvisxl.safetensors");
  });

  it("gives a stack its differences and a single workflow its type", () => {
    expect(factChips(STACK).map((c) => c.label)).toEqual([
      "+ face detailer",
      "+ upscale 2×",
    ]);
    const single = { ...STACK, stack_size: 1, differs_by: [], imported: true };
    expect(factChips(single).map((c) => c.label)).toEqual([
      "txt2img",
      "imported",
    ]);
  });

  // A hidden card is only ever drawn because somebody ticked *Show hidden
  // workflows* (F7). Unmarked it is indistinguishable from a card that was
  // never hidden, in the one grid it was deliberately kept out of — and it
  // leads the row because the row clips to "+N".
  it("leads with `hidden`, on a lone card and on a stack alike", () => {
    expect(
      factChips({ ...STACK, stack_size: 1, differs_by: [], hidden: true }).map(
        (c) => c.label,
      ),
    ).toEqual(["hidden", "txt2img"]);
    expect(factChips({ ...STACK, hidden: true }).map((c) => c.label)).toEqual([
      "hidden",
      "+ face detailer",
      "+ upscale 2×",
    ]);
    // And a card nobody hid says nothing about it.
    expect(factChips({ ...STACK, hidden: false }).map((c) => c.label)).toEqual([
      "+ face detailer",
      "+ upscale 2×",
    ]);
  });
});

describe("cardAccessibleName", () => {
  it("names every LoRA with workflow or recipe, since +N is not a control", () => {
    const loras = Array.from({ length: 5 }, (_, i) => ({
      name: `lora-${i}`,
      mark: "structural",
    }));
    const name = cardAccessibleName({
      ...STACK,
      loras: [...loras, { mark: "recipe" }],
    });
    for (let i = 0; i < 5; i++)
      expect(name).toContain(`lora-${i}, workflow LoRA`);
    expect(name).toContain("recipe LoRA slot");
    expect(name).toContain("stack of 6 workflows");
    expect(name).toContain("differs by: + face detailer, + upscale 2×");
    expect(name).toContain("rated 4.8 of 5");
  });

  it("says so when there are no LoRAs and no rating", () => {
    const name = cardAccessibleName({
      name: "Bare",
      loras: [],
      picture_count: 1,
    });
    expect(name).toBe("Bare, no LoRAs, 1 picture");
  });

  it("says a card's models were not read rather than that it has none", () => {
    const name = cardAccessibleName({
      name: "Template",
      models: [],
      loras: [],
      variant_count: 0,
      picture_count: 0,
    });
    expect(name).toBe(
      "Template, base model not read, LoRAs not read, 0 pictures",
    );
  });
});

describe("checkpointUnread / lorasUnread", () => {
  // The rule: on a card with a recipe an empty row is a fact, and on a card
  // without one it is a silence. Each row is asked separately, because the
  // recovery can find one and miss the other.
  const withRecipe = { variant_count: 1, models: [], loras: [] };
  const noRecipe = { variant_count: 0, models: [], loras: [] };

  it("is true only where there is no recipe behind the empty row", () => {
    expect(checkpointUnread(noRecipe)).toBe(true);
    expect(lorasUnread(noRecipe)).toBe(true);
    expect(checkpointUnread(withRecipe)).toBe(false);
    expect(lorasUnread(withRecipe)).toBe(false);
  });

  it("answers per row, so a half-read card still declines to claim", () => {
    // A LoRA recovered and the loader beside it missed: `models` is empty on
    // a workflow that has one, and "No checkpoint" would be a lie. This is the
    // case a single whole-card flag got wrong.
    const halfRead = {
      variant_count: 0,
      models: [],
      loras: [{ name: "add_detail.safetensors", mark: "structural" }],
    };
    expect(checkpointUnread(halfRead)).toBe(true);
    expect(lorasUnread(halfRead)).toBe(false);

    // And the other way round: a base model found, no LoRA loader in the file.
    const modelOnly = {
      variant_count: 0,
      models: [{ name: "base.safetensors", kind: "checkpoint" }],
      loras: [],
    };
    expect(checkpointUnread(modelOnly)).toBe(false);
    expect(lorasUnread(modelOnly)).toBe(true);
  });

  it("only counts a BASE model, never an accessory slot", () => {
    // A recovered VAE is not an answer to "which model is this workflow
    // about": `checkpointModel` walks `BASE_MODEL_KINDS` and this is not one.
    const vaeOnly = {
      variant_count: 0,
      models: [{ name: "ae.safetensors", kind: "vae" }],
      loras: [],
    };
    expect(checkpointUnread(vaeOnly)).toBe(true);
  });

  it("reads a card carrying no variant_count at all as having a recipe", () => {
    // Every card the route serves carries one, so the missing case is a
    // caller that predates the field - and it must not be told that every
    // card it holds is unread.
    expect(checkpointUnread({ models: [], loras: [] })).toBe(false);
    expect(lorasUnread({ models: [], loras: [] })).toBe(false);
  });
});

describe("ratingLabel", () => {
  it("reads as out of five", () => {
    expect(ratingLabel(4.9)).toBe("4.9 of 5");
    expect(ratingLabel(null)).toBeNull();
    // 0 is unrated, not a zero-star rating.
    expect(ratingLabel(0)).toBeNull();
  });
});

// ── Which model the card is ABOUT ────────────────────────────────────────
//
// Slot order is document order, so "the first slot" is whatever the graph
// happened to list first. #1449 shipped a Flux card whose name row read
// `flux1-dev` and whose model row read `ae.safetensors` - its VAE - because
// the name had learned this and the row had not.
describe("checkpointModel", () => {
  const flux = {
    models: [
      { name: "ae.safetensors", kind: "vae" },
      { name: "t5xxl_fp16.safetensors", kind: "clip" },
      { name: "flux1-dev.safetensors", kind: "unet" },
    ],
  };

  it("takes the base model, never the first slot", () => {
    expect(checkpointModel(flux).name).toBe("flux1-dev.safetensors");
  });

  it("prefers a checkpoint over a unet where a graph carries both", () => {
    const both = {
      models: [
        { name: "flux1-dev.safetensors", kind: "unet" },
        { name: "juggernautXL.safetensors", kind: "checkpoint" },
      ],
    };
    expect(checkpointModel(both).kind).toBe("checkpoint");
  });

  it("is null for a graph that loads no base model at all", () => {
    // The row then says "No checkpoint" rather than naming an accessory, and
    // the accessible name leaves the model out instead of announcing a VAE.
    const accessories = {
      models: [
        { name: "ae.safetensors", kind: "vae" },
        { name: "4x-UltraSharp.pth", kind: "upscale" },
      ],
    };
    expect(checkpointModel(accessories)).toBe(null);
    expect(cardAccessibleName({ ...accessories, name: "X" })).not.toContain(
      "ae.safetensors",
    );
  });

  it("skips a base slot whose name was forgotten", () => {
    const forgotten = {
      models: [
        { name: null, kind: "checkpoint" },
        { name: "flux1-dev.safetensors", kind: "unet" },
      ],
    };
    expect(checkpointModel(forgotten).name).toBe("flux1-dev.safetensors");
  });
});

// ── One fact, one voice ──────────────────────────────────────────────────
describe("the type chip", () => {
  it("reads the served label, so it matches a generated name", () => {
    const labels = factChips({
      type: "txt2img",
      type_label: "Text to Image",
    }).map((chip) => chip.label);

    expect(labels).toContain("Text to Image");
    // The card's name row says "realvisxl: Text to Image"; the chip saying
    // `txt2img` beside it is one fact in two vocabularies.
    expect(labels).not.toContain("txt2img");
  });

  it("falls back to the token when the payload has no label", () => {
    expect(factChips({ type: "inpaint" }).map((c) => c.label)).toContain(
      "inpaint",
    );
  });
});
