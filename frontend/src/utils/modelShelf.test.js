// The name fallback chain, size and location reduction the shelf row is built
// on. The name cases are the ones that matter: 37% of real adapters carry no
// title at all, so the "derived" branch is the common path, not the fallback.

import { describe, it, expect } from "vitest";
import { contrastRatio } from "./contrastAudit.js";
import { SET_COLORS } from "./setAppearance";
import {
  assignmentMarks,
  bandGroups,
  bandUsage,
  baseModelKey,
  collapseStacks,
  withEmptyFolders,
  cleanAssetName,
  deriveModelName,
  formatModelSize,
  generatedMark,
  importReceipt,
  markBackground,
  markForeground,
  locationState,
  modelName,
  movableCopies,
  offlineFolders,
  stackReceipt,
  trainingStep,
  withFolderSignals,
  FOLDER_TIERS,
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
      { text: "Clementine", state: "named" },
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
    ).toEqual({ text: "Foxglove Char", state: "derived" });
  });

  it("separates the file's own string from one we made", () => {
    // Nothing survived the strip, so what is shown IS the filename — a
    // different piece of news from `derived`, and the row says which.
    expect(modelName({ filename: "000002750.safetensors" })).toEqual({
      text: "000002750.safetensors",
      state: "from-file",
    });
  });

  it("returns nothing to show when there is nothing to show", () => {
    // Deliberately empty rather than the old `no name in file`, which looked
    // like a name, sorted like one and read as inert. The row draws this state
    // as a field asking to be filled (#897).
    expect(modelName({})).toEqual({ text: "", state: "needs-a-name" });
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

describe("offlineFolders", () => {
  const at = (folderId, state, folderPath = `/mnt/${folderId}`) => ({
    folder_id: folderId,
    folder_path: folderPath,
    relpath: "a.safetensors",
    state,
  });

  it("names a wholly unreachable folder and counts its rows", () => {
    expect(
      offlineFolders([
        { id: 1, locations: [at(7, "unreachable", "/mnt/usb")] },
        { id: 2, locations: [at(7, "unreachable", "/mnt/usb")] },
      ]),
    ).toEqual([{ folderId: 7, path: "/mnt/usb", count: 2 }]);
  });

  it("is silent about a folder that was readable", () => {
    // One `present` copy means the drive IS plugged in, and one `missing` copy
    // means the folder WAS read — a different fact, and not one to fold into
    // "offline". Either disqualifies the whole folder.
    expect(
      offlineFolders([
        { id: 1, locations: [at(7, "unreachable")] },
        { id: 2, locations: [at(7, "present")] },
      ]),
    ).toEqual([]);
    expect(
      offlineFolders([
        { id: 1, locations: [at(7, "unreachable")] },
        { id: 2, locations: [at(7, "missing")] },
      ]),
    ).toEqual([]);
  });

  it("counts a row once however many copies of it a folder holds", () => {
    const twice = {
      id: 1,
      locations: [
        at(7, "unreachable"),
        { ...at(7, "unreachable"), relpath: "b" },
      ],
    };
    expect(offlineFolders([twice])[0].count).toBe(1);
  });

  it("leaves a row alone whose other folder is fine", () => {
    // The offline folder is still named — the copy on it genuinely cannot be
    // read — but the row is usable, which is the reader's actual question and
    // the reason this is a mount-level statement rather than a row-level one.
    const rows = [
      { id: 1, locations: [at(7, "unreachable"), at(8, "present")] },
    ];
    expect(offlineFolders(rows)).toEqual([
      { folderId: 7, path: "/mnt/7", count: 1 },
    ]);
  });

  it("orders by path, so the banner reads the same every render", () => {
    const rows = [
      { id: 1, locations: [at(9, "unreachable", "/mnt/zed")] },
      { id: 2, locations: [at(8, "unreachable", "/mnt/alpha")] },
    ];
    expect(offlineFolders(rows).map((f) => f.path)).toEqual([
      "/mnt/alpha",
      "/mnt/zed",
    ]);
  });

  it("counts numerically, so /mnt/2 is not filed after /mnt/10", () => {
    // The shelf's other lists already collate this way; a bare `localeCompare`
    // here read `/mnt/10, /mnt/2` and made the banner disagree with the rows.
    const rows = [
      { id: 1, locations: [at(1, "unreachable", "/mnt/disk10")] },
      { id: 2, locations: [at(2, "unreachable", "/mnt/Disk2")] },
    ];
    expect(offlineFolders(rows).map((f) => f.path)).toEqual([
      "/mnt/Disk2",
      "/mnt/disk10",
    ]);
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
  it("splits the drive into three segments that carve up what is used", () => {
    // The shelf's share is a PART of what is used, so `other` is the rest of
    // the used space and not the whole of it. Drawing `used` and `shelf` as
    // two overlaid fills was the original shape and it left the reader unable
    // to tell which of the two a given boundary marked.
    const usage = bandUsage({
      totalBytes: 1000,
      freeBytes: 250,
      shelfBytes: 500,
    });
    expect(usage.shelfPct).toBe(50);
    expect(usage.otherPct).toBe(25);
    expect(usage.freePct).toBe(25);
    expect(usage.usedPct).toBe(75);
  });

  it("keeps the three segments summing to exactly one full track", () => {
    // Laid out in a row rather than overlaid, so anything short of 100 opens a
    // sliver of bare track at the right-hand end that reads as free space.
    for (const band of [
      { totalBytes: 3, freeBytes: 1, shelfBytes: 1 },
      { totalBytes: 1024 ** 4, freeBytes: 7, shelfBytes: 123456789 },
      { totalBytes: 1000, freeBytes: 1000, shelfBytes: 0 },
      { totalBytes: 1000, freeBytes: 0, shelfBytes: 1000 },
    ]) {
      const usage = bandUsage(band);
      expect(usage.shelfPct + usage.otherPct + usage.freePct).toBeCloseTo(
        100,
        10,
      );
      expect(usage.otherPct).toBeGreaterThanOrEqual(0);
    }
  });

  it("measures low space in checkpoints that would fit, not in percentages", () => {
    const GB = 1024 ** 3;
    // 400 GB left on a 4 TB drive is a tenth of it, and dozens more models.
    // The fraction rule would warn here, which is the cry-wolf case.
    expect(
      bandUsage({ totalBytes: 4000 * GB, freeBytes: 400 * GB, shelfBytes: 0 })
        .lowFree,
    ).toBe(false);
    // The same drive with room for two more checkpoints IS worth saying, even
    // though it is only 1% and no fraction rule set for the small disk would
    // have caught it.
    expect(
      bandUsage({ totalBytes: 4000 * GB, freeBytes: 40 * GB, shelfBytes: 0 })
        .lowFree,
    ).toBe(true);
    // And 60 GB on a small SSD is nearly half of it but still not low, which
    // is the direction a fraction rule gets backwards.
    expect(
      bandUsage({ totalBytes: 128 * GB, freeBytes: 60 * GB, shelfBytes: 0 })
        .lowFree,
    ).toBe(false);
  });

  it("never lets a drive report more free space than it holds", () => {
    // Thin provisioning and transparent compression (ZFS, btrfs) report this
    // by design, and a network mount's statvfs can simply be wrong. `free` and
    // `total` are two separate reads, so nothing upstream reconciles them.
    // Unclamped this gives freePct 150 and a segment running off the track.
    const usage = bandUsage({
      totalBytes: 1000,
      freeBytes: 1500,
      shelfBytes: 0,
    });
    expect(usage.freePct).toBe(100);
    expect(usage.usedPct).toBe(0);
    expect(usage.otherPct).toBe(0);
    expect(usage.shelfPct).toBe(0);
    expect(usage.shelfPct + usage.otherPct + usage.freePct).toBe(100);
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
    // `other` must absorb the clamp and stay at zero rather than going
    // negative, which would draw a segment growing leftwards.
    const usage = bandUsage({
      totalBytes: 1000,
      freeBytes: 900,
      shelfBytes: 5000,
    });
    expect(usage.shelfPct).toBe(usage.usedPct);
    expect(usage.otherPct).toBe(0);
  });
});

describe("withEmptyFolders", () => {
  const group = (path) => ({
    key: path,
    label: path,
    labelKind: "path",
    folderId: 1,
    rows: [{ id: 1 }],
  });

  it("adds the registered folder that produced no group", () => {
    // The managed store is exactly this on a fresh install: registered, ruled
    // to be the default drop destination, and holding nothing — so it has no
    // rows, no group, and no way to be seen.
    const arranged = withEmptyFolders(
      [group("/models/loras")],
      [
        { id: 1, path: "/models/loras", last_checked: "2026-08-10T00:00:00Z" },
        { id: 2, path: "/models/store", last_checked: "2026-08-10T00:00:00Z" },
      ],
    );
    expect(arranged.map((g) => g.label)).toEqual([
      "/models/loras",
      "/models/store",
    ]);
    expect(arranged[1].rows).toEqual([]);
    expect(arranged[1].folderId).toBe(2);
  });

  it("does not call a folder empty just because a filter hid its models", () => {
    // The failure this guards: `groups` is built from the VISIBLE rows, so a
    // folder full of adapters has no group while Show is narrowed to
    // checkpoints. Reading "no group" as "no models" would print "No models in
    // this folder" over a folder holding ninety.
    const arranged = withEmptyFolders(
      [],
      [
        {
          id: 1,
          path: "/models/loras",
          last_checked: "2026-08-10T00:00:00Z",
          file_count: 91,
        },
        {
          id: 2,
          path: "/models/store",
          last_checked: "2026-08-10T00:00:00Z",
          file_count: 0,
        },
      ],
    );
    expect(arranged.map((g) => g.label)).toEqual(["/models/store"]);
  });

  it("still shows a folder nothing has looked in, whatever its count says", () => {
    // `file_count` is 0 for an unscanned folder too, but the two are different
    // facts and only this one is the owner's to act on.
    const [only] = withEmptyFolders(
      [],
      [{ id: 1, path: "/models/new", last_checked: null, file_count: 0 }],
    );
    expect(only.emptyReason).toBe("unscanned");
  });

  it("keeps 'never scanned' apart from 'scanned and empty'", () => {
    // Only one of the two is the owner's to act on, and `last_checked` is the
    // discriminator: a folder that has never been walked has no count to be
    // zero.
    const [never, walked] = withEmptyFolders(
      [],
      [
        { id: 1, path: "/a", last_checked: null },
        { id: 2, path: "/b", last_checked: "2026-08-10T00:00:00Z" },
      ],
    );
    expect(never.emptyReason).toBe("unscanned");
    expect(walked.emptyReason).toBe("empty");
  });

  it("never duplicates a folder that already has rows", () => {
    const arranged = withEmptyFolders(
      [group("/models/loras")],
      [{ id: 1, path: "/models/loras", last_checked: null }],
    );
    expect(arranged).toHaveLength(1);
    expect(arranged[0].rows).toHaveLength(1);
  });

  it("returns the groups untouched when every folder has some", () => {
    // Identity, not a copy: the caller bands this result, and a needless new
    // array would invalidate every downstream computed on each keystroke.
    const groups = [group("/models/loras")];
    expect(withEmptyFolders(groups, [{ id: 1, path: "/models/loras" }])).toBe(
      groups,
    );
  });
});

describe("withFolderSignals", () => {
  const group = (folderId, path) => ({
    key: path,
    label: path,
    labelKind: "path",
    folderId,
    rows: [],
  });

  const devices = (entries) => {
    const byFolder = new Map();
    for (const device of entries) {
      for (const id of device.folder_ids) byFolder.set(id, device);
    }
    return byFolder;
  };

  it("gives folders on one drive one rail colour, and two drives two", () => {
    // The whole point of the hue: "these are on the same physical disk" read
    // off the header, without opening the folders dialog.
    const [a, b, c] = withFolderSignals(
      [
        group(1, "/mnt/fast/loras"),
        group(2, "/mnt/fast/ckpt"),
        group(3, "/ext"),
      ],
      {
        folders: [
          { id: 1, path: "/mnt/fast/loras", kind: "user" },
          { id: 2, path: "/mnt/fast/ckpt", kind: "user" },
          { id: 3, path: "/ext", kind: "user" },
        ],
        deviceByFolderId: devices([
          { device_id: "9", label: "FastModels", folder_ids: [1, 2] },
          { device_id: "12", label: "Archive", folder_ids: [3] },
        ]),
      },
    );
    expect(a.drive.rail).toBe(b.drive.rail);
    expect(c.drive.rail).not.toBe(a.drive.rail);
    // The chip carries the identity; the colour is only a grouping hint.
    expect(a.drive.label).toBe("FastModels");
    expect(c.drive.label).toBe("Archive");
  });

  it("numbers the drives by device id, not by the order groups arrive in", () => {
    // A group order that decided the colours would repaint every folder's rail
    // when a filter emptied a group or a new disk was plugged in.
    const context = {
      folders: [
        { id: 1, path: "/a", kind: "user" },
        { id: 2, path: "/b", kind: "user" },
      ],
      deviceByFolderId: devices([
        { device_id: "aaa", folder_ids: [1] },
        { device_id: "bbb", folder_ids: [2] },
      ]),
    };
    const forward = withFolderSignals(
      [group(1, "/a"), group(2, "/b")],
      context,
    );
    const reversed = withFolderSignals(
      [group(2, "/b"), group(1, "/a")],
      context,
    );
    expect(reversed[1].drive.rail).toBe(forward[0].drive.rail);
    expect(reversed[0].drive.rail).toBe(forward[1].drive.rail);
  });

  it("gives an unmeasured drive no rail colour rather than inventing one", () => {
    // We do not know which disk this is on, and a colour would claim a grouping
    // nothing measured.
    const [only] = withFolderSignals([group(7, "/net/models")], {
      folders: [{ id: 7, path: "/net/models", kind: "user" }],
      deviceByFolderId: devices([{ device_id: null, folder_ids: [7] }]),
    });
    expect(only.drive).toBe(null);
  });

  it("states each tier in a shape and a word, never in a colour", () => {
    const [managed, locked, plain] = withFolderSignals(
      [group(1, "/store"), group(2, "/hf"), group(3, "/mine")],
      {
        folders: [
          { id: 1, path: "/store", kind: "managed" },
          { id: 2, path: "/hf", kind: "foreign" },
          { id: 3, path: "/mine", kind: "user" },
        ],
      },
    );
    expect(managed.icon).toBe(FOLDER_TIERS.managed.icon);
    expect(managed.chip).toBe("Managed");
    expect(locked.icon).toBe(FOLDER_TIERS.foreign.icon);
    expect(locked.chip).toBe("Locked");
    // The unmarked case: chipping every header would hide the two that matter.
    expect(plain.icon).toBe(FOLDER_TIERS.user.icon);
    expect(plain.chip).toBe("");
  });

  it("marks an unreachable folder offline, and keeps its tier chip", () => {
    const [only] = withFolderSignals([group(2, "/hf")], {
      folders: [{ id: 2, path: "/hf", kind: "foreign" }],
      offlineFolderIds: new Set([2]),
    });
    expect(only.offline).toBe(true);
    // A different GLYPH, so the reachability survives greyscale — and the tier
    // is still stated, because a locked folder is locked whether or not the
    // disk is plugged in.
    expect(only.icon).toBe("mdi-lan-disconnect");
    expect(only.chip).toBe("Locked");
  });

  it("nests a folder that sits inside another registered one, one level only", () => {
    const [parent, child, sibling, deeper] = withFolderSignals(
      [
        group(1, "/models"),
        group(2, "/models/loras"),
        group(3, "/models-old"),
        group(4, "/models/loras/flux"),
      ],
      {
        folders: [
          { id: 1, path: "/models", kind: "user" },
          { id: 2, path: "/models/loras", kind: "user" },
          { id: 3, path: "/models-old", kind: "user" },
          { id: 4, path: "/models/loras/flux", kind: "user" },
        ],
      },
    );
    expect(parent.nested).toBe(false);
    expect(child.nested).toBe(true);
    // A prefix that is not a path boundary is a different folder, not a child.
    expect(sibling.nested).toBe(false);
    // Two registered folders above it, still one step: the indent says "inside
    // another folder", which is a yes or a no.
    expect(deeper.nested).toBe(true);
  });

  it("leaves a group that is not a folder alone", () => {
    // "No registered copy" has no folder id, no disk and no tier — decorating
    // it would put a drive rail over the one group that exists because there is
    // no drive.
    const [only] = withFolderSignals([{ key: "none", label: "x", rows: [] }], {
      folders: [{ id: 1, path: "/models", kind: "user" }],
    });
    expect(only.tier).toBe(null);
    expect(only.drive).toBe(null);
    expect(only.chip).toBe("");
    expect(only.nested).toBe(false);
  });
});

describe("baseModelKey", () => {
  it("prefers the folded label, so four spellings make one bucket", () => {
    const rows = [
      { base_model: "sdxl_base_v1-0", base_model_folded: "SDXL 1.0" },
      { base_model: "SDXL", base_model_folded: "SDXL 1.0" },
      { base_model: "stable diffusion xl", base_model_folded: "SDXL 1.0" },
    ];
    expect(new Set(rows.map(baseModelKey)).size).toBe(1);
  });

  it("falls back to the raw string the server did not recognise", () => {
    // Not "not set": an unrecognised base model is a real value the owner can
    // still group and filter by. Sweeping it into the unset bucket would hide
    // every private or brand-new base.
    expect(
      baseModelKey({
        base_model: "my private base v3",
        base_model_folded: null,
      }),
    ).toBe("my private base v3");
  });

  it("reports nothing for a row that records nothing", () => {
    expect(baseModelKey({ base_model: null, base_model_folded: null })).toBe(
      "",
    );
    expect(baseModelKey(undefined)).toBe("");
  });
});

describe("importReceipt", () => {
  it("reports the failures, which the server decides per file", () => {
    expect(
      importReceipt({
        run_name: "Clementine",
        files: [
          { status: "imported" },
          { status: "imported" },
          { status: "failed" },
        ],
      }),
    ).toBe(
      "Imported 2 checkpoints from Clementine. 1 checkpoint could not be copied and was left in the run.",
    );
  });

  it("never says the run was deleted when nothing landed", () => {
    // The server unlinks last and only after each row is committed, so the two
    // cannot both be true. Saying it anyway would tell the reader their run was
    // destroyed for nothing.
    expect(
      importReceipt({
        run_name: "Clementine",
        deleted_source: true,
        files: [{ status: "failed" }],
      }),
    ).toBe(
      "Nothing was imported from Clementine. 1 checkpoint could not be copied and was left in the run.",
    );
  });

  it("agrees its verb with the count, in both directions", () => {
    // Written twice and got wrong twice: `moveReceipt` had this exact bug, it
    // was fixed there, and then this function repeated it a few hundred lines
    // later. Both numbers are asserted so the next copy cannot.
    expect(
      importReceipt({
        run_name: "R",
        files: [{ status: "failed" }],
      }),
    ).toContain("1 checkpoint could not be copied and was left in the run.");
    expect(
      importReceipt({
        run_name: "R",
        files: [{ status: "failed" }, { status: "failed" }],
      }),
    ).toContain("2 checkpoints could not be copied and were left in the run.");
  });

  it("says the run is gone when it is", () => {
    expect(
      importReceipt({
        run_name: "Clementine",
        deleted_source: true,
        files: [{ status: "imported" }],
      }),
    ).toBe(
      "Imported 1 checkpoint from Clementine. The run's own files have been removed.",
    );
  });
});

describe("collapseStacks", () => {
  function member(id, stackId, position, overrides = {}) {
    return {
      id,
      stack_id: stackId,
      stack_position: position,
      filename: `Run_00000${position}00.safetensors`,
      ...overrides,
    };
  }

  it("folds a run into one row carrying its members", () => {
    // Without this a six-step run reads as six unrelated adapters, which is
    // what the shelf did until F5.
    const out = collapseStacks([
      member(1, 7, 0),
      member(2, 7, 1),
      member(3, 7, 2),
      { id: 4, stack_id: null },
    ]);
    expect(out.map((r) => r.id)).toEqual([1, 4]);
    expect(out[0].memberCount).toBe(3);
    expect(out[0].memberIds).toEqual([1, 2, 3]);
  });

  it("keeps the cover's own fields on the folded row", () => {
    const out = collapseStacks([
      member(2, 7, 1, { display_name: "Step" }),
      member(1, 7, 0, { display_name: "Cover" }),
    ]);
    expect(out[0].display_name).toBe("Cover");
  });

  it("folds onto the lowest surviving position when the cover is filtered out", () => {
    // A run half-hidden by a base-model filter is still a run. Dropping it
    // because position 0 is not in view would make the filter lie about what is
    // on disk.
    const out = collapseStacks([member(2, 7, 1), member(3, 7, 2)]);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe(2);
    expect(out[0].memberCount).toBe(2);
  });

  it("counts what is SHOWN, not what the payload claims", () => {
    // A badge reading 6 over a strip that opens to 2 would be describing rows
    // the reader cannot reach.
    const out = collapseStacks([
      member(1, 7, 0, { member_count: 6 }),
      member(2, 7, 1, { member_count: 6 }),
    ]);
    expect(out[0].memberCount).toBe(2);
  });

  it("leaves unstacked rows exactly as they were", () => {
    const loose = { id: 9, stack_id: null, filename: "solo.safetensors" };
    expect(collapseStacks([loose])[0]).toBe(loose);
  });

  it("keeps two different runs apart", () => {
    const out = collapseStacks([
      member(1, 7, 0),
      member(2, 8, 0),
      member(3, 7, 1),
    ]);
    expect(out.map((r) => r.id)).toEqual([1, 2]);
    expect(out[0].memberIds).toEqual([1, 3]);
  });
});

/** `hsl(h s% l%)` -> `#rrggbb`, so `contrastRatio` can read the rendered tile. */
function hslToHex(css) {
  const [h, s, l] = css.match(/[\d.]+/g).map(Number);
  const sat = s / 100;
  const lig = l / 100;
  const c = (1 - Math.abs(2 * lig - 1)) * sat;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = lig - c / 2;
  const [r, g, b] = (
    h < 60
      ? [c, x, 0]
      : h < 120
        ? [x, c, 0]
        : h < 180
          ? [0, c, x]
          : h < 240
            ? [0, x, c]
            : h < 300
              ? [x, 0, c]
              : [c, 0, x]
  ).map((v) => Math.round((v + m) * 255));
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

describe("the generated mark's contrast", () => {
  it("is legible on every one of the 48, at WCAG AA", () => {
    // Measured with the SHIPPED `contrastRatio` from `utils/contrastAudit.js`,
    // the same one the theme audit uses, so this agrees with the rest of the
    // codebase about what AA means rather than carrying its own arithmetic.
    //
    // Measured on the RAW palette first: 22 of the 48 clear neither white nor
    // near-black, because the mid-tones are unreachable from either end. That
    // is why the tile pins lightness instead of picking an ink.
    const failures = SET_COLORS.filter(({ value }) => {
      const tile = hslToHex(markBackground(value));
      return contrastRatio(markForeground(), tile) < 4.5;
    }).map(({ value, label }) => `${label} ${value}`);

    expect(failures).toEqual([]);
  });

  it("keeps the palette entry's hue, which is what carries the identity", () => {
    // Renormalising for legibility must not turn two different base models
    // into the same tile.
    const red = markBackground("#e53935");
    const cyan = markBackground("#00acc1");
    expect(red).not.toBe(cyan);
    expect(red).toMatch(/^hsl\(\d+ 55% 30%\)$/);
  });

  it("hands the ink out with the colour, so the pair cannot separate", () => {
    const mark = generatedMark({ display_name: "A B", base_model: "x" });
    expect(
      contrastRatio(mark.ink, hslToHex(mark.color)),
    ).toBeGreaterThanOrEqual(4.5);
  });
});

describe("stackReceipt", () => {
  it("reports the runs that landed and the ones that did not", () => {
    expect(stackReceipt(3, 0)).toBe("Grouped 3 runs.");
    expect(stackReceipt(2, 1)).toBe(
      "Grouped 2 runs. 1 run could not be grouped; something changed them first.",
    );
  });

  it("says the files are unchanged when nothing was grouped", () => {
    expect(stackReceipt(0, 2)).toBe(
      "Nothing was grouped. 2 runs could not be, and the files are unchanged.",
    );
    expect(stackReceipt(0, 0)).toBe("Nothing to group.");
  });
});

describe("trainingStep", () => {
  it("reads the same suffix the name derivation strips", () => {
    expect(trainingStep("JimmyCarr_000000500.safetensors")).toBe(500);
    expect(trainingStep("ohwx_woman-step00004500.safetensors")).toBe(4500);
  });

  it("reports no step for a bare final, and for a version suffix", () => {
    // `v2` is not training bookkeeping — `deriveModelName` keeps it, so this
    // must not read it as a step.
    expect(trainingStep("JimmyCarr.safetensors")).toBe(null);
    expect(trainingStep("portrait_mix_v2.safetensors")).toBe(null);
  });
});

describe("movableCopies", () => {
  const folders = new Map([
    [1, { id: 1, kind: "user", movable: "per_item" }],
    [2, { id: 2, kind: "foreign", owner: "pixlstash", movable: "root_only" }],
    [3, { id: 3, kind: "foreign", movable: "external" }],
  ]);

  function locRow(locations, fileSize = 100) {
    return { file_size: fileSize, locations };
  }

  it("takes only the copies that are actually on this machine", () => {
    // `missing` says the folder was readable and the file was not in it;
    // `unreachable` says we could not look. Sending either would ask the server
    // to copy bytes nobody has seen.
    const { items } = movableCopies([
      locRow([
        { folder_id: 1, relpath: "a.st", state: "present" },
        { folder_id: 1, relpath: "b.st", state: "missing" },
        { folder_id: 1, relpath: "c.st", state: "unreachable" },
      ]),
    ]);
    expect(items).toEqual([{ folder_id: 1, relpath: "a.st" }]);
  });

  it("is per COPY, so one model in two folders offers two", () => {
    // `model_file`'s key is `(folder_id, relpath)`, and the size is summed off
    // the model because the hub records one `file_size` for every copy of it.
    const { items, totalBytes } = movableCopies(
      [
        locRow(
          [
            { folder_id: 1, relpath: "a.st", state: "present" },
            { folder_id: 1, relpath: "dup/a.st", state: "present" },
          ],
          500,
        ),
      ],
      folders,
    );
    expect(items).toHaveLength(2);
    expect(totalBytes).toBe(1000);
  });

  it("leaves PixlStash's own engines where they are", () => {
    // Declared rather than scanned, and every engine loader looks for them at a
    // fixed path: moving one out breaks the tagger and re-downloads it.
    const { items } = movableCopies(
      [locRow([{ folder_id: 2, relpath: "tagger.onnx", state: "present" }])],
      folders,
    );
    expect(items).toEqual([]);
  });

  it("leaves a folder shared with other software alone", () => {
    // The HuggingFace cache and insightface's store are not ours to take from.
    const { items } = movableCopies(
      [locRow([{ folder_id: 3, relpath: "m.st", state: "present" }])],
      folders,
    );
    expect(items).toEqual([]);
  });

  it("applies only the present rule when no folder map is given", () => {
    const { items } = movableCopies([
      locRow([{ folder_id: 2, relpath: "tagger.onnx", state: "present" }]),
    ]);
    expect(items).toHaveLength(1);
  });
});

describe("assignmentMarks", () => {
  const attach = (type, id) => ({ entity_type: type, entity_id: id });

  it("takes the entity's own colour, so a character is one hue app-wide", () => {
    const [mark] = assignmentMarks([attach("character", 7)], {
      characters: [{ id: 7, name: "Ada", character_color: "#e91e63" }],
    });
    expect(mark.hue).toBe("#e91e63");
    expect(mark.label).toBe("Character: Ada");
    expect(mark.initials).toBe("AD");
  });

  it("keys a colourless entity on its id, never on its place in the fan", () => {
    // Positional colour would repaint every remaining mark when one attachment
    // is removed, which is the failure `generatedMark` documents for models.
    const lists = {
      sets: [
        { id: 4, name: "Beach" },
        { id: 9, name: "Studio" },
      ],
    };
    const pair = assignmentMarks([attach("set", 4), attach("set", 9)], lists);
    const alone = assignmentMarks([attach("set", 9)], lists);
    expect(alone[0].hue).toBe(pair[1].hue);
    expect(SET_COLORS.map((c) => c.value)).toContain(alone[0].hue);
  });

  it("gives every mark an identity beside its colour", () => {
    // The greyscale test, as arithmetic: strip the hue and each mark still
    // carries a name and a glyph, so nothing is distinguished by colour alone.
    const marks = assignmentMarks(
      [attach("character", 1), attach("set", 1)],
      {},
    );
    expect(marks.map((m) => m.label)).toEqual(["Character: #1", "Set: #1"]);
    expect(marks.every((m) => m.initials)).toBe(true);
    // The type is in the noun, never in the hue — two entities can and do
    // collide on a colour, which is exactly why colour is only a hint.
    expect(marks.every((m) => SET_COLORS.some((c) => c.value === m.hue))).toBe(
      true,
    );
  });

  it("fans three and then counts, front-to-back", () => {
    const marks = assignmentMarks(
      [1, 2, 3, 4, 5].map((id) => attach("character", id)),
      {},
    );
    expect(marks).toHaveLength(3);
    expect(marks[2].more).toBe(3);
    expect(marks[2].label).toBe("Character: #3, Character: #4, Character: #5");
    expect(marks.map((m) => m.z)).toEqual([3, 2, 1]);
  });

  it("returns nothing for a model assigned to nothing", () => {
    expect(assignmentMarks([], {})).toEqual([]);
    expect(assignmentMarks(undefined)).toEqual([]);
  });

  it("does not serve a stale name after the entity list is replaced", () => {
    // The id maps are cached per LIST REFERENCE, because this is called once
    // per shelf row and rebuilding them there cost rows x entities (#915
    // review). The entity store replaces the array on every refresh rather than
    // mutating it, so a rename must come through on the next render.
    const before = [{ id: 7, name: "Ada" }];
    const after = [{ id: 7, name: "Ada Lovelace" }];
    const marks = (characters) =>
      assignmentMarks([attach("character", 7)], { characters })[0].label;
    expect(marks(before)).toBe("Character: Ada");
    expect(marks(before)).toBe("Character: Ada");
    expect(marks(after)).toBe("Character: Ada Lovelace");
  });
});
