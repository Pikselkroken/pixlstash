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
});

describe("ratingLabel", () => {
  it("reads as out of five", () => {
    expect(ratingLabel(4.9)).toBe("4.9 of 5");
    expect(ratingLabel(null)).toBeNull();
    // 0 is unrated, not a zero-star rating.
    expect(ratingLabel(0)).toBeNull();
  });
});
