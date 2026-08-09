// The name fallback chain, size and location reduction the shelf row is built
// on. The name cases are the ones that matter: 37% of real adapters carry no
// title at all, so the "derived" branch is the common path, not the fallback.

import { describe, it, expect } from "vitest";
import {
  cleanAssetName,
  deriveModelName,
  formatModelSize,
  locationState,
  modelName,
} from "./modelShelf";

describe("cleanAssetName", () => {
  it("matches the Python it mirrors", () => {
    // Parity with `clean_asset_name`, whose output is baked into stored
    // embeddings and therefore may not drift.
    expect(cleanAssetName("z_image_turbo_bf16.safetensors")).toBe(
      "z image turbo bf16",
    );
    expect(cleanAssetName("/a/b/portrait-mix.safetensors")).toBe(
      "portrait mix",
    );
  });
});

describe("deriveModelName", () => {
  it("drops the training bookkeeping, keeps a version suffix", () => {
    // The three doctest cases from `derive_model_name`. The bare-digit rule
    // needs five digits so `000002750` goes and the `2` in `v2` stays;
    // dropping that distinction merges six checkpoints of one run into six
    // unrelated-looking rows, or renames every `v2` file.
    expect(deriveModelName("JimmyCarr_000002750.safetensors")).toBe(
      "JimmyCarr",
    );
    expect(deriveModelName("ohwx_woman-step00004500.safetensors")).toBe(
      "ohwx woman",
    );
    expect(deriveModelName("portrait_mix_v2.safetensors")).toBe(
      "portrait mix v2",
    );
  });

  it("returns nothing when nothing survives the strip", () => {
    expect(deriveModelName("000002750.safetensors")).toBe("");
  });
});

describe("modelName", () => {
  it("prefers the name somebody gave it, and says nobody did", () => {
    expect(modelName({ display_name: "Clementine", filename: "x.st" })).toEqual(
      { text: "Clementine", derived: false },
    );
  });

  it("derives from the filename and marks it derived", () => {
    // The mark is the whole point: a guess must stay distinguishable from a
    // choice, because `display_name IS NULL` is the backend's work queue.
    expect(
      modelName({ display_name: null, filename: "Foxglove_Char_000000250.safetensors" }),
    ).toEqual({ text: "Foxglove Char", derived: true });
  });

  it("falls back to the raw filename rather than to a blank", () => {
    expect(modelName({ filename: "000002750.safetensors" })).toEqual({
      text: "000002750.safetensors",
      derived: true,
    });
  });

  it("never returns an empty string", () => {
    expect(modelName({}).text).not.toBe("");
  });
});

describe("formatModelSize", () => {
  it("scales past MB", () => {
    // The two local `formatBytes` copies in the app top out at MB, which
    // understates a 4.3 GB adapter by three orders.
    expect(formatModelSize(179426130)).toBe("171.1 MB");
    expect(formatModelSize(4.3 * 1024 ** 3)).toBe("4.3 GB");
    expect(formatModelSize(512)).toBe("512 B");
  });

  it("says nothing rather than zero when the size is unknown", () => {
    expect(formatModelSize(null)).toBe("");
    expect(formatModelSize(undefined)).toBe("");
  });
});

describe("locationState", () => {
  it("treats one present copy as healthy", () => {
    expect(
      locationState([{ state: "missing" }, { state: "present" }]),
    ).toBe("present");
  });

  it("keeps 'we could not look' apart from 'it is not there'", () => {
    // `missing` is a fact and may be acted on; `unreachable` is the absence of
    // one and must never be recorded as a deletion.
    expect(locationState([{ state: "unreachable" }])).toBe("unreachable");
    expect(locationState([{ state: "missing" }])).toBe("missing");
    expect(
      locationState([{ state: "missing" }, { state: "unreachable" }]),
    ).toBe("unreachable");
  });

  it("reports no copies at all as its own state", () => {
    expect(locationState([])).toBe("forgotten");
    expect(locationState(undefined)).toBe("forgotten");
  });
});
