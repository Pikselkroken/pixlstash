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
  checkpoint: "realvisXL_v5",
  loras: [{ name: "lightning-8step" }, { name: "", recipe: true }],
  differsBy: ["+ face detailer", "+ upscale 2×"],
  type: "txt2img",
  pictureCount: 184,
  rating: 4.85,
  stackSize: 6,
};

describe("card chips", () => {
  it("draws a recipe slot dashed and a workflow LoRA solid", () => {
    expect(loraChips(STACK).map((c) => [c.label, c.variant])).toEqual([
      ["lightning-8step", "solid"],
      ["recipe LoRA", "dashed"],
    ]);
  });

  it("gives a stack its differences and a single workflow its type", () => {
    expect(factChips(STACK).map((c) => c.label)).toEqual([
      "+ face detailer",
      "+ upscale 2×",
    ]);
    const single = { ...STACK, stackSize: 1, differsBy: [], imported: true };
    expect(factChips(single).map((c) => c.label)).toEqual([
      "txt2img",
      "imported",
    ]);
  });
});

describe("cardAccessibleName", () => {
  it("names every LoRA with workflow or recipe, since +N is not a control", () => {
    const loras = Array.from({ length: 5 }, (_, i) => ({ name: `lora-${i}` }));
    const name = cardAccessibleName({
      ...STACK,
      loras: [...loras, { recipe: true }],
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
      pictureCount: 1,
    });
    expect(name).toBe("Bare, no LoRAs, 1 picture");
  });
});

describe("ratingLabel", () => {
  it("reads as out of five", () => {
    expect(ratingLabel(4.9)).toBe("4.9 of 5");
    expect(ratingLabel(null)).toBeNull();
  });
});
