// The name fallback chain, size and location reduction the shelf row is built
// on. The name cases are the ones that matter: 37% of real adapters carry no
// title at all, so the "derived" branch is the common path, not the fallback.

import { describe, it, expect } from "vitest";
import {
  bandGroups,
  bandUsage,
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
      modelName({
        display_name: null,
        filename: "Foxglove_Char_000000250.safetensors",
      }),
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
    expect(locationState([{ state: "missing" }, { state: "present" }])).toBe(
      "present",
    );
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

describe("bandGroups", () => {
  const group = (folderId, label) => ({
    key: label,
    label,
    labelKind: "path",
    folderId,
    rows: [],
  });

  const drive = (id, mount, folderIds, extra = {}) => ({
    device_id: id,
    mount_point: mount,
    total_bytes: 1000,
    free_bytes: 400,
    shelf_bytes: 200,
    folder_ids: folderIds,
    ...extra,
  });

  it("makes a band's folders contiguous even when they arrive apart", () => {
    // The alphabetical order of the folders interleaves the two drives. A band
    // drawn over a non-contiguous run would claim a grouping the list has not
    // got, so the groups are re-ordered rather than the header repeated.
    const devices = new Map([
      [1, drive("A", "/mnt/fast", [1, 3])],
      [3, drive("A", "/mnt/fast", [1, 3])],
      [2, drive("B", "/mnt/bulk", [2])],
    ]);
    const arranged = bandGroups(
      [
        group(1, "/mnt/fast/a"),
        group(2, "/mnt/bulk/b"),
        group(3, "/mnt/fast/c"),
      ],
      devices,
    );
    expect(arranged.map((g) => g.band.label)).toEqual([
      "/mnt/bulk",
      "/mnt/fast",
      "/mnt/fast",
    ]);
    expect(arranged.map((g) => g.bandStart)).toEqual([true, true, false]);
  });

  it("leaves a group that names no folder unbanded", () => {
    // "No registered copy" is the group for a model whose folders have all been
    // forgotten. It is not on a drive, so a disk glyph and a capacity line over
    // it would describe the one group that exists because there is no disk.
    const orphan = {
      key: "\u0000unset",
      label: "No registered copy",
      labelKind: "name",
      folderId: null,
      rows: [],
    };
    const arranged = bandGroups(
      [orphan, group(1, "/mnt/fast/a")],
      new Map([[1, drive("A", "/mnt/fast", [1])]]),
    );
    expect(arranged.map((g) => g.label)).toEqual([
      "/mnt/fast/a",
      "No registered copy",
    ]);
    expect(arranged.at(-1).band).toBe(null);
    expect(arranged.at(-1).bandStart).toBe(false);
  });

  it("bands an unmeasured folder alone and puts it last", () => {
    // Two drives we cannot stat are not thereby the same drive, and an offline
    // one is not what the reader is scanning for space on.
    const arranged = bandGroups(
      [group(1, "/net/one"), group(2, "/net/two"), group(3, "/local")],
      new Map([[3, drive("A", "/", [3])]]),
    );
    expect(arranged[0].band.label).toBe("/");
    expect(arranged[0].band.measured).toBe(true);
    expect(arranged.slice(1).map((g) => g.band.key)).toEqual(["f:1", "f:2"]);
    expect(arranged.slice(1).every((g) => g.bandStart)).toBe(true);
  });
});

describe("bandUsage", () => {
  it("reports the shelf's share as part of what is used, never beside it", () => {
    // Two fills in one track: if the shelf's share were measured against free
    // space the two could sum past the drive.
    const usage = bandUsage({
      totalBytes: 1000,
      freeBytes: 250,
      shelfBytes: 500,
    });
    expect(usage.usedPct).toBe(75);
    expect(usage.shelfPct).toBe(50);
    expect(usage.shelfPct).toBeLessThanOrEqual(usage.usedPct);
  });

  it("refuses to draw a meter for a drive it could not measure", () => {
    // Null rather than zero: an empty bar reads as a drive with nothing on it,
    // which is the opposite of "we do not know".
    expect(bandUsage({ totalBytes: null, freeBytes: null })).toBe(null);
    expect(bandUsage({ totalBytes: 0, freeBytes: 0 })).toBe(null);
    expect(bandUsage(undefined)).toBe(null);
  });

  it("never lets a stale shelf figure overflow the track", () => {
    // `shelf_bytes` is counted from the hub and the capacity from the disk, so
    // a scan mid-flight can make the first exceed the second for a moment.
    const usage = bandUsage({
      totalBytes: 1000,
      freeBytes: 900,
      shelfBytes: 5000,
    });
    expect(usage.shelfPct).toBe(usage.usedPct);
  });
});
