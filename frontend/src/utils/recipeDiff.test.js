import { describe, expect, it } from "vitest";

import { lookLoraDiff, recipeDiff, shortName } from "./recipeDiff";

const DEFAULT = {
  models: [
    { address: "core:Loader/ckpt_name", kind: "checkpoint", filename: "sdxl/juggernaut.safetensors" },
    { address: "core:VAE/vae_name", kind: "vae", filename: "sdxl_vae.safetensors" },
  ],
  loras: [
    { filename: "style/film_grain.safetensors", sha256: "f".repeat(64), strength: 0.5 },
    { filename: "detail_tweaker.safetensors", sha256: "d".repeat(64), strength: 0.6 },
  ],
  values: [
    { label: "Steps", slot_label: "core:Sampler", input_name: "steps", value: 30, provenance: "best" },
    { label: "CFG", slot_label: "core:Sampler", input_name: "cfg", value: 7, provenance: "edited" },
  ],
};

const texts = (diff) => diff.segments.map((segment) => segment.text);

describe("recipeDiff", () => {
  it("prints every difference in the fixed order", () => {
    const diff = recipeDiff(
      {
        keep_seed: true,
        seed: 1234,
        negative: "blurry",
        models: [
          { address: "core:Loader/ckpt_name", filename: "realvisXL.safetensors" },
          { address: "core:VAE/vae_name", filename: "xl_vae.safetensors" },
        ],
        loras: [
          { filename: "detail_tweaker.safetensors", sha256: "d".repeat(64), strength: 0.9 },
          { filename: "chars/character_v2.safetensors", sha256: "c".repeat(64), strength: 0.8 },
        ],
        overrides: { "core:Sampler/steps": 40, "core:Sampler/cfg": 7 },
      },
      DEFAULT,
    );
    expect(texts(diff)).toEqual([
      "realvisXL",
      "VAE: xl_vae",
      "+ character_v2 0.8",
      "detail_tweaker 0.6 → 0.9",
      "without film_grain",
      "Steps 40",
      "seed 1234",
    ]);
    // The arrow is its own quiet part, so the view can dim it.
    const strength = diff.segments[3].parts;
    expect(strength.filter((part) => part.quiet).map((part) => part.text)).toEqual([" → "]);
    expect(diff.params).toEqual([
      { slot_label: "core:Sampler", input_name: "steps", label: "Steps", from: "30", to: 40 },
    ]);
  });

  it("says nothing about inherited models, an empty LoRA list, or an unkept seed", () => {
    const diff = recipeDiff({ models: null, loras: [], seed: 9, keep_seed: false }, DEFAULT);
    expect(diff.segments).toEqual([]);
  });

  it("matches LoRAs as a multiset by digest", () => {
    const sha = "d".repeat(64);
    const diff = recipeDiff(
      {
        loras: [
          { filename: "renamed_on_disk.safetensors", sha256: sha, strength: 0.6 },
          { filename: "detail_tweaker.safetensors", sha256: sha, strength: 0.3 },
          { filename: "film_grain.safetensors", sha256: "f".repeat(64), strength: 0.5 },
        ],
      },
      DEFAULT,
    );
    // One default load of the file, two in the recipe: one is added.
    expect(texts(diff)).toEqual(["+ detail_tweaker 0.3"]);
  });

  it("compares a parameter by the input it fills, not its slot spelling", () => {
    const diff = recipeDiff(
      { overrides: { "KSampler/steps": 30, "KSampler/denoise": 0.5 } },
      DEFAULT,
    );
    // steps equals the default; denoise has no default row, and is named by input.
    expect(texts(diff)).toEqual(["denoise 0.5"]);
    expect(diff.params).toEqual([]);
  });

  it("has nothing to compare against without a default", () => {
    expect(recipeDiff({ loras: [] }, null)).toBeNull();
  });

  it("shortens a file name to what a person calls it", () => {
    expect(shortName("a\\b/c.v2.safetensors")).toBe("c.v2");
  });
});

describe("a default LoRA the owner added has no filename", () => {
  const withNameless = {
    ...DEFAULT,
    loras: [...DEFAULT.loras, { filename: null, sha256: "a".repeat(64), strength: 1 }],
  };

  it("pairs by digest when the recipe carries digests", () => {
    const recipe = { loras: withNameless.loras.map((lora) => ({ ...lora, filename: lora.filename || "ada.safetensors" })) };
    expect(recipeDiff(recipe, withNameless).segments).toEqual([]);
  });

  it("names it as unnamed when the recipe, with digests, goes without it", () => {
    const recipe = { loras: DEFAULT.loras };
    expect(recipeDiff(recipe, withNameless).segments.map((s) => s.text)).toEqual([
      "without an unnamed LoRA",
    ]);
  });

  it("says the LoRAs were not compared when the recipe has no digests", () => {
    const recipe = { loras: [{ filename: "ada.safetensors", strength: 1 }] };
    expect(recipeDiff(recipe, withNameless).segments.map((s) => s.text)).toEqual([
      "LoRAs not compared",
    ]);
  });

  it("gives a look no LoRA diff, rather than a nameless 'without'", () => {
    expect(lookLoraDiff({ loras: [{ filename: "ada.safetensors" }] }, withNameless)).toBe(null);
  });
});

describe("digests", () => {
  it("pair whatever their case", () => {
    const recipe = {
      loras: DEFAULT.loras.map((lora) => ({ ...lora, filename: "renamed.safetensors", sha256: lora.sha256.toUpperCase() })),
    };
    expect(recipeDiff(recipe, DEFAULT).segments).toEqual([]);
  });
});

describe("lookLoraDiff", () => {
  it("names the LoRAs a look adds and leaves out, by short name", () => {
    const segments = lookLoraDiff(
      { loras: [{ filename: "Film_Grain.safetensors" }, { filename: "x/ada.safetensors" }] },
      DEFAULT,
    );
    expect(segments.map((segment) => segment.text)).toEqual(["+ ada", "without detail_tweaker"]);
  });
});
