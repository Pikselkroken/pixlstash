// The name fallback chain, size and location reduction the shelf row is built
// on. The name cases are the ones that matter: 37% of real adapters carry no
// title at all, so the "derived" branch is the common path, not the fallback.

import { describe, it, expect } from "vitest";
import { contrastRatio } from "./contrastAudit.js";
import { ICON_CARDS, SET_COLORS } from "./setAppearance";
import {
  isInFoldersTree,
  adapterKindKey,
  adapterKindLabel,
  assignmentRing,
  deletableModels,
  undeletableNotice,
  trashName,
  companionsSentences,
  RING_STYLES,
  baseModelCell,
  baseModelKey,
  capabilityLabel,
  collapseStacks,
  comfyuiLoadsTitle,
  comfyuiMarks,
  layoutKind,
  modelDestinations,
  unsaidTypes,
  comfyuiName,
  comfyuiReadFor,
  copyPathsTitle,
  isMisplaced,
  SHELF_TYPES,
  modelFileTitle,
  dateColumnKey,
  quantBadge,
  recordedTriggerWords,
  triggerChip,
  quantFromFilename,
  modelDate,
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
  moveMechanism,
  allAlreadyIn,
  offlineFolders,
  stackReceipt,
  unstackReceipt,
  releaseReceipt,
  modelVersion,
  versionSortKey,
  trainingStep,
  CAPABILITY_LABELS,
  capabilityIcon,
} from "./modelShelf";

describe("capabilityIcon", () => {
  it("gives every named capability its own glyph", () => {
    // The bug: the feature axis drew its own glyph on every header, so eight
    // headers read as one star. A duplicate here would put two features back
    // under one mark, and a missing one drops that header to the star again.
    const icons = Object.keys(CAPABILITY_LABELS).map(capabilityIcon);
    expect(icons.filter(Boolean)).toHaveLength(icons.length);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("returns nothing for a capability this build has never seen", () => {
    // The caller falls back to the axis's glyph. Inventing one would be the
    // confident wrong answer.
    expect(capabilityIcon("segmenter")).toBe("");
    expect(capabilityIcon(null)).toBe("");
  });
});

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
    expect(deriveModelName("JimmyVehicle_000002750.safetensors")).toBe(
      "JimmyVehicle",
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

// (filename, the name a row shows, the canonical quant id or null).
//
// **Mirrors `QUANT_VOCABULARY` in `tests/test_model_file_classification.py`
// case for case.** That is what the parity guarantee between the two halves
// of the parser means: the shelf derives its names here and the workflow card
// is served them from there, so a vocabulary the two disagree about is two
// spellings of one model, a screen apart.
const QUANT_VOCABULARY = [
  ["model_fp32.safetensors", "model", "fp32"],
  ["model_f32.safetensors", "model", "fp32"],
  ["model_fp16.safetensors", "model", "fp16"],
  ["z_image_turbo_bf16.safetensors", "z image turbo", "bf16"],
  ["t5xxl_fp8_e4m3fn.safetensors", "t5xxl", "fp8_e4m3"],
  ["clip_l_fp8_e5m2.safetensors", "clip l", "fp8_e5m2"],
  ["model_fp8.safetensors", "model", "fp8"],
  ["flux_nvfp4_awq.safetensors", "flux", "nvfp4"],
  ["model_nf4.safetensors", "model", "nf4"],
  ["model_int8.safetensors", "model", "int8"],
  ["model_i4.safetensors", "model", "int4"],
  ["flux1-dev-Q4_K_M.gguf", "flux1 dev", "q4_k_m"],
  ["Qwen_Image-Q6_K.gguf", "Qwen Image", "q6_k"],
  ["flux_q8_0.gguf", "flux", "q8_0"],
  ["flux1-dev-IQ4_XS.gguf", "flux1 dev", "iq4_xs"],
  ["flux1-dev-IQ3_M.gguf", "flux1 dev", "iq3_m"],
  ["Qwen-Image-IQ2_XXS.gguf", "Qwen Image", "iq2_xxs"],
  ["Qwen_Image-Q3_K_XL.gguf", "Qwen Image", "q3_k_xl"],
  ["flux_iq4_nl.gguf", "flux", "iq4_nl"],
  ["model-TQ1_0.gguf", "model", "tq1_0"],
  ["model-TQ2_0.gguf", "model", "tq2_0"],
  ["model-MXFP4.gguf", "model", "mxfp4"],
  ["umt5_xxl_fp8_e4m3fn_scaled.safetensors", "umt5 xxl", "fp8_e4m3"],
  ["model-step00004500-fp16.safetensors", "model", "fp16"],
  // Mixed case, because every shelf fixture in this repo is lowercase and a
  // case-folding bug in the lookup would otherwise pass the whole suite.
  ["Flux1-Dev-FP8_E4M3FN.safetensors", "Flux1 Dev", "fp8_e4m3"],
  ["Clementine_BF16.safetensors", "Clementine", "bf16"],
  // ComfyUI template spellings (#2061).
  ["Qwen_Image_Edit_2511_FP8Mixed.safetensors", "Qwen Image Edit 2511", "fp8"],
  ["qwen_3_4b_fp8_mixed.safetensors", "qwen 3 4b", "fp8"],
  ["gemma_3_12B_it_fp4_mixed.safetensors", "gemma 3 12B it", "fp4"],
  ["z_image_int8_convrot.safetensors", "z image", "int8"],
  ["Wan21-WanMove_fp8_scaled_e4m3fn_KJ.safetensors", "Wan21 WanMove", "fp8_e4m3"],
  ["Wan_fp8_e4m3fn_scaled_KJ.safetensors", "Wan", "fp8_e4m3"],
  ["qwen_pe_t2i.int8_convrot.safetensors", "qwen pe t2i", "int8"],
  // A version after the precision stays in the name.
  ["ltx-upscaler-x2-bf16-1.0.safetensors", "ltx upscaler x2 1.0", "bf16"],
  ["qwen-360-int8-bf16-v2.safetensors", "qwen 360 v2", "bf16"],
];

// Names that must come back untouched: `scaled`, `fast`, `m`, `l`, `1` and `0`
// are ordinary tokens in real model names.
const QUANT_NON_VOCABULARY = [
  ["some_model_scaled.safetensors", "some model scaled"],
  ["clementine_fast.safetensors", "clementine fast"],
  ["clementine_m.safetensors", "clementine m"],
  ["sdxl_1.safetensors", "sdxl 1"],
  ["sd_xl_base_1.0.safetensors", "sd xl base 1.0"],
  ["portrait_mix_v2.safetensors", "portrait mix v2"],
  ["juggernaut_xl_v9.safetensors", "juggernaut xl v9"],
  ["Anything-XL.safetensors", "Anything XL"],
  ["clementine_mixed.safetensors", "clementine mixed"],
  ["clementine_kj.safetensors", "clementine kj"],
  // `constructor` is on every Object, so a bare `in` lookup ate it as a quant.
  ["clementine_constructor.safetensors", "clementine constructor"],
];

describe("the quant postfix", () => {
  it.each(QUANT_VOCABULARY)("%s reads as %s", (filename, name, quant) => {
    expect(deriveModelName(filename)).toBe(name);
    expect(quantFromFilename(filename)).toBe(quant);
  });

  it.each(QUANT_NON_VOCABULARY)("%s keeps its name", (filename, name) => {
    // Without the "at least one real quant token" guard,
    // `some_model_scaled` silently becomes `some model`.
    expect(deriveModelName(filename)).toBe(name);
    expect(quantFromFilename(filename)).toBe(null);
  });

  it("falls back to the filename when the name was only a quant", () => {
    expect(deriveModelName("nvfp4_awq.safetensors")).toBe("");
    expect(modelName({ filename: "nvfp4_awq.safetensors" })).toEqual({
      text: "nvfp4_awq.safetensors",
      state: "from-file",
    });
  });
});

describe("triggerChip", () => {
  it("draws nothing for a model with no trigger word at all", () => {
    expect(triggerChip({ trigger_words: [] })).toBeNull();
    // A row from before the field was a list, or one that never carried it.
    expect(triggerChip({})).toBeNull();
    expect(triggerChip({ trigger_words: "ohwx" })).toBeNull();
  });

  it("shows the first recorded word and counts the rest", () => {
    expect(
      triggerChip({ trigger_words: ["ohwx"], trigger_words_source: "recorded" }),
    ).toEqual({ label: "ohwx", title: "Trigger word: ohwx", isDefault: false });
    expect(
      triggerChip({
        trigger_words: ["ohwx", "woman", "portrait"],
        trigger_words_source: "recorded",
      }),
    ).toEqual({
      label: "ohwx +2",
      title: "Trigger words: ohwx, woman, portrait",
      isDefault: false,
    });
  });

  it("caps the hover list of a file that records its whole tag table", () => {
    const words = Array.from({ length: 40 }, (_, i) => `tag${i}`);
    const chip = triggerChip({
      trigger_words: words,
      trigger_words_source: "recorded",
    });
    expect(chip.label).toBe("tag0 +39");
    expect(chip.title).toContain("tag11 and 28 more");
    expect(chip.title).not.toContain("tag12");
  });

  it("marks a person's name as the default it is, and never as on record", () => {
    const row = { trigger_words: ["Ada"], trigger_words_source: "character" };
    const chip = triggerChip(row);
    expect(chip.isDefault).toBe(true);
    expect(chip.label).toBe("Ada");
    expect(chip.title).toContain("the name of the person");
    // What a bulk write counts as replaced: a default is not stored, so
    // writing over it loses nothing.
    expect(recordedTriggerWords(row)).toEqual([]);
    expect(
      recordedTriggerWords({
        trigger_words: ["ohwx"],
        trigger_words_source: "recorded",
      }),
    ).toEqual(["ohwx"]);
  });
});

describe("quantBadge", () => {
  it("turns the served id into words, not the raw dtype", () => {
    // The API serves `fp8_e4m3`, folded from the header's own `f8_e4m3`. A
    // chip reading the dtype is the drift this map exists to stop.
    expect(quantBadge("fp8_e4m3")).toEqual({
      label: "FP8",
      title: "FP8 E4M3",
    });
    expect(quantBadge("bf16")).toEqual({ label: "BF16", title: "bfloat16" });
  });

  it("shows a GGUF level verbatim, because the level is the name", () => {
    expect(quantBadge("q4_k_m")).toEqual({
      label: "Q4_K_M",
      title: "GGUF Q4_K_M quantisation",
    });
  });

  it("keeps current TQ GGUF types distinguishable", () => {
    expect(quantBadge("tq1_0")).toEqual({
      label: "TQ1_0",
      title: "GGUF TQ1_0 quantisation",
    });
    expect(quantBadge("mxfp4")).toEqual({
      label: "MXFP4",
      title: "mixed FP4",
    });
  });

  it("shows an id it has never seen rather than swallowing it", () => {
    expect(quantBadge("f64")).toEqual({ label: "F64", title: "Stored as F64" });
  });

  it("gives a model with no recorded precision NO badge", () => {
    // Null and not an empty chip: a blank badge reads as a rendering gap and
    // an `UNKNOWN` one reads like a fact nobody established.
    expect(quantBadge(null)).toBe(null);
    expect(quantBadge("")).toBe(null);
    expect(quantBadge("   ")).toBe(null);
    expect(quantBadge(undefined)).toBe(null);
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

  it("shows a checkpoint's matched base-model label, still as ours", () => {
    // Drawn as `derived`: the label is our reading of the filename, not a
    // name anybody chose, so the "needs a name" queue is untouched.
    expect(
      modelName({
        display_name: null,
        filename: "flux1-dev-fp8.safetensors",
        matched_name: "FLUX.1 dev",
      }),
    ).toEqual({ text: "FLUX.1 dev", state: "derived" });
    // A name somebody gave still wins over it.
    expect(
      modelName({ display_name: "My Flux", matched_name: "FLUX.1 dev" }).text,
    ).toBe("My Flux");
  });

  it("separates the file's own string from one we made", () => {
    // Nothing survived the strip, so what is shown IS the filename - a
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

describe("adapterKindLabel", () => {
  it("spells the algorithm one way, whatever the trainer wrote", () => {
    expect(adapterKindLabel("lora")).toBe("LoRA");
    expect(adapterKindLabel("LORA")).toBe("LoRA");
    // `model.kind` is free text and the edit dialog takes it verbatim. An
    // untrimmed value would sort to the TOP of a grouped shelf, because
    // `localeCompare` puts a leading space before every letter.
    expect(adapterKindLabel("  lokr  ")).toBe("LoKr");
  });

  it("shows an algorithm it has not heard of rather than hiding it", () => {
    // Same rule as `capabilityLabel`: a LyCORIS variant that ships after this
    // table should appear under its own name, not under "Unknown".
    expect(adapterKindLabel("glora")).toBe("glora");
    expect(adapterKindLabel(null)).toBe("");
    expect(adapterKindLabel(undefined)).toBe("");
  });

  it("returns a STRING for a key that names something on Object.prototype", () => {
    // `kind` is free text, and a plain index on an object literal answers
    // `constructor` with a FUNCTION - truthy, so a `|| key` fallback never
    // fires. That value became a group label, where `localeCompare` throws and
    // takes the whole `groups` computed with it.
    expect(adapterKindLabel("constructor")).toBe("constructor");
    expect(adapterKindLabel("__proto__")).toBe("__proto__");
  });
});

describe("adapterKindKey", () => {
  it("folds what everything else keys on, and keeps it a key", () => {
    // Exported because the group key, the facet, the filter, the remembered
    // selection and the edit dialog all have to agree on ONE value. The label
    // is a separate question: this stays the stored vocabulary.
    expect(adapterKindKey(" LoRA ")).toBe("lora");
    expect(adapterKindKey("lora")).toBe("lora");
    expect(adapterKindKey("  ")).toBe("");
    expect(adapterKindKey(null)).toBe("");
  });
});

describe("capabilityLabel", () => {
  it("names a capability in the screen's words and passes an unknown through", () => {
    expect(capabilityLabel("scorer")).toBe("Quality score");
    expect(capabilityLabel("newthing")).toBe("newthing");
  });

  it("returns a STRING for a key that names something on Object.prototype", () => {
    // Same hole as `adapterKindLabel`'s, and this input comes off the wire.
    expect(capabilityLabel("constructor")).toBe("constructor");
    expect(capabilityLabel("toString")).toBe("toString");
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

describe("modelFileTitle", () => {
  const SHA = "ab12".repeat(16);

  it("puts the whole hash under the paths", () => {
    expect(
      modelFileTitle({
        sha256: SHA,
        locations: [{ folder_path: "/home/me/models", relpath: "a.st" }],
      }),
    ).toBe(`/home/me/models/a.st\nSHA-256: ${SHA}`);
  });

  it("is the hash alone for a set member, which has no locations", () => {
    expect(modelFileTitle({ sha256: SHA })).toBe(`SHA-256: ${SHA}`);
  });

  it("says a checkpoint is still waiting for its hash", () => {
    expect(modelFileTitle({ sha256: null, hash_pending: true })).toBe(
      "SHA-256: not read yet",
    );
  });

  it("does not say a row that never gets a hash is waiting for one", () => {
    // An engine, or a cached Hugging Face repo: a directory nothing hashes.
    expect(modelFileTitle({ sha256: null, hash_pending: false })).toBe("");
  });

  it("claims nothing about a set member whose shelf row is not loaded", () => {
    // Such a member has no `sha256` field at all; "not read yet" would be a
    // statement about the hasher that nothing here knows to be true.
    expect(modelFileTitle({ id: 3, filename: "a.st" })).toBe("");
    expect(modelFileTitle(undefined)).toBe("");
  });
});

describe("copyPathsTitle", () => {
  it("names every folder a copy sits in, one line each", () => {
    // The whole point: under `groupBy: 'base_model'` no header names a folder,
    // so a model on two disks has to say both here or say neither anywhere.
    expect(
      copyPathsTitle([
        {
          folder_path: "/home/me/models/lora",
          relpath: "sdxl/foo.safetensors",
        },
        { folder_path: "/media/me/spare/", relpath: "foo.safetensors" },
      ]),
    ).toBe(
      "/home/me/models/lora/sdxl/foo.safetensors\n" +
        "/media/me/spare/foo.safetensors",
    );
  });

  it("keeps a Windows path a Windows path, both halves of it", () => {
    // The two halves come from different places - the folder as registered, the
    // relpath as the scanner wrote it - so a forward-slashed relpath under a
    // backslashed folder is the case that comes back half-slashed.
    expect(
      copyPathsTitle([
        {
          folder_path: "C:\\Users\\me\\models",
          relpath: "sdxl/foo.safetensors",
        },
      ]),
    ).toBe("C:\\Users\\me\\models\\sdxl\\foo.safetensors");
    // A POSIX folder leaves the relpath alone: a backslash there is a legal
    // character in a filename, not a separator to rewrite.
    expect(
      copyPathsTitle([{ folder_path: "/home/me/models", relpath: "a\\b.st" }]),
    ).toBe("/home/me/models/a\\b.st");
  });

  it("says what is at each path, so an absent copy is not drawn as a present one", () => {
    // The row spends a rail, a glyph and a word telling the three absences
    // apart (#898). Rendering four states as four identical lines is the one
    // place they would read the same.
    expect(
      copyPathsTitle([
        { folder_path: "/home/me/models", relpath: "a.st", state: "present" },
        { folder_path: "/media/me/spare", relpath: "a.st", state: "missing" },
        { folder_path: "/mnt/ext", relpath: "a.st", state: "unreachable" },
      ]),
    ).toBe(
      "/home/me/models/a.st\n" +
        "/media/me/spare/a.st · not where it was\n" +
        "/mnt/ext/a.st · out of reach",
    );
  });

  it("skips a copy missing either half rather than half-naming it", () => {
    // Both are NOT NULL on the wire, so this is a broken row - and `a.st` on
    // its own answers "where is this file" with the one thing that is not a
    // location.
    expect(
      copyPathsTitle([
        { relpath: "a.st" },
        { folder_path: "/home/me/models" },
        { folder_path: "/home/me/models", relpath: "b.st" },
      ]),
    ).toBe("/home/me/models/b.st");
  });

  it("appends nothing for a state that names something on Object.prototype", () => {
    // `state` comes off the wire, and a plain index answers `constructor` with
    // a FUNCTION - truthy, so the note would be Object's constructor source
    // pasted after the path. Same hole `labelFrom` closes for the two label
    // tables. A state this build has simply never seen appends nothing either:
    // machine vocabulary after a path reads as a fault.
    expect(
      copyPathsTitle([
        {
          folder_path: "/home/me/models",
          relpath: "a.st",
          state: "constructor",
        },
        { folder_path: "/home/me/models", relpath: "b.st", state: "sundered" },
      ]),
    ).toBe("/home/me/models/a.st\n/home/me/models/b.st");
  });

  it("says nothing when every copy has been forgotten", () => {
    // The caller binds no tooltip at all rather than an empty one, and the
    // file line already carries the words for this state.
    expect(copyPathsTitle([])).toBe("");
    expect(copyPathsTitle(undefined)).toBe("");
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

  it("does not call an engine nobody has needed yet a fault", () => {
    // #926: a declared engine that was never fetched used to reach the shelf as
    // `missing` - error rail, "The file is not where it was" - on a machine
    // with nothing wrong with it.
    expect(locationState([{ state: "not_downloaded" }])).toBe("not_downloaded");
    // One genuinely missing copy still states the fault...
    expect(
      locationState([{ state: "not_downloaded" }, { state: "missing" }]),
    ).toBe("missing");
    // ...and so does a state this build has never heard of.
    expect(locationState([{ state: "banana" }])).toBe("missing");
  });

  it("does not call a copy the reader removed themselves a fault", () => {
    // #1439: a copy removed to keep one of several has a row ON PURPOSE - it is
    // what keeps a recipe naming that file resolving to the model - so drawing
    // it as `missing` would report a fault about the tidy-up the reader just
    // asked for. This is what the folder axis hands in: that draw stands for the
    // removed copy alone.
    expect(locationState([{ state: "removed" }])).toBe("removed");
    // A model that still has a copy is simply fine...
    expect(locationState([{ state: "removed" }, { state: "present" }])).toBe(
      "present",
    );
    // ...and a genuinely missing copy beside a removed one is still a fault,
    // because reporting the pair as a tidy-up would hide one.
    expect(locationState([{ state: "removed" }, { state: "missing" }])).toBe(
      "missing",
    );
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
    // means the folder WAS read - a different fact, and not one to fold into
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
    // The offline folder is still named - the copy on it genuinely cannot be
    // read - but the row is usable, which is the reader's actual question and
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

  it("prefers the identified label, which is what the server filters on", () => {
    expect(
      baseModelKey({
        base_model: null,
        base_model_folded: null,
        base_model_canonical: "Illustrious XL",
      }),
    ).toBe("Illustrious XL");
    // A typo the server read as Illustrious groups under Illustrious, not
    // under its own misspelling.
    expect(
      baseModelKey({
        base_model: "Ilustrius",
        base_model_folded: null,
        base_model_canonical: "Illustrious XL",
      }),
    ).toBe("Illustrious XL");
  });
});

describe("baseModelCell", () => {
  it("shows what the file or a person said, untagged", () => {
    for (const source of ["declared", "user"]) {
      expect(
        baseModelCell({
          base_model: "sdxl_base_v1-0",
          base_model_canonical: "SDXL 1.0",
          base_model_source: source,
        }),
      ).toEqual({ text: "sdxl_base_v1-0", guess: null });
    }
    // modelspec declared it, and there is no raw string to show.
    expect(
      baseModelCell({
        base_model: null,
        base_model_canonical: "SDXL 1.0",
        base_model_source: "declared",
      }),
    ).toEqual({ text: "SDXL 1.0", guess: null });
  });

  it("tags every guess and shows the label, not the raw string", () => {
    for (const source of ["filename", "filename_fuzzy"]) {
      const cell = baseModelCell({
        base_model: null,
        base_model_canonical: "Sana",
        base_model_source: source,
      });
      expect(cell.text).toBe("Sana");
      expect(cell.guess).toMatch(/filename/);
    }
    const typo = baseModelCell({
      base_model: "Ilustrius",
      base_model_canonical: "Illustrious XL",
      base_model_source: "declared_fuzzy",
    });
    expect(typo.text).toBe("Illustrious XL");
    expect(typo.guess).toContain("Ilustrius");
  });

  it("says a Civitai answer came from Civitai, without calling it a guess", () => {
    const cell = baseModelCell({
      base_model: "Illustrious",
      base_model_canonical: "Illustrious XL",
      base_model_source: "civitai",
    });
    expect(cell.text).toBe("Illustrious");
    expect(cell.guess).toBeNull();
    expect(cell.note).toMatch(/^From Civitai, matched by file hash/);
  });

  it("is empty when nothing is recorded, including a cleared guess", () => {
    expect(baseModelCell({ base_model: null })).toEqual({
      text: "",
      guess: null,
    });
    expect(
      baseModelCell({
        base_model: null,
        base_model_canonical: null,
        base_model_source: "user",
      }),
    ).toEqual({ text: "", guess: null });
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

  it("sorts a member with no position LAST, as the server does", () => {
    // NULL sorts first in SQLite too, and the server spells `stack_position IS
    // NULL` to stop that. `?? 0` here put an unpositioned member level with
    // the cover and, on a stable sort, ahead of it - so the two ends disagreed
    // about which file is the face of the run.
    const out = collapseStacks([
      member(1, 7, 0),
      member(2, 7, null),
      member(3, 7, 1),
    ]);
    expect(out[0].memberIds).toEqual([1, 3, 2]);
    expect(out[0].id).toBe(1);
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

describe("the generated mark's initials", () => {
  const initials = (display_name) => generatedMark({ display_name }).initials;

  it("skips prepositions between words", () => {
    expect(initials("World of Wordcraft")).toBe("WW");
    expect(initials("Lord_Of_The_Rings")).toBe("LR");
    expect(initials("image-to-video")).toBe("IV");
  });

  it("keeps a leading one, which is part of the name", () => {
    expect(initials("In-Context LoRA")).toBe("IC");
  });

  it("never skips its way down to a single word", () => {
    expect(initials("The Thing")).toBe("TT");
    expect(initials("x-in")).toBe("XI");
    expect(initials("of the")).toBe("OT");
  });

  it("matches whole words only", () => {
    expect(initials("Forest Inpaint")).toBe("FI");
  });

  it("takes letters from words that start with one", () => {
    // A number is the version, never an initial: the old rule drew the first
    // of these as S5.
    expect(initials("sd3.5 large controlnet canny")).toBe("SL");
    expect(initials("Stable Diffusion 3.5-Large")).toBe("SD");
    expect(initials("Wan 2.2 high noise")).toBe("WH");
    // One lettered word gives its first two letters, never a digit.
    expect(initials("SDXL 1.0")).toBe("SD");
    expect(initials("flux1")).toBe("FL");
    expect(initials("4x-UltraSharp")).toBe("UL");
  });

  it("takes a letter outside the BMP whole, never half of one", () => {
    expect(initials("𝐀pple 𝐁anana")).toBe("𝐀𝐁");
    expect(initials("𝐀𝐁𝐂")).toBe("𝐀𝐁");
    expect(initials("1😀")).toBe("1😀");
  });
});

describe("the generated mark's version tab", () => {
  const version = (display_name) => generatedMark({ display_name }).version;

  it("keeps the dot of an x.y version", () => {
    expect(version("Stable Diffusion 3.5-Large")).toBe("3.5");
    expect(version("sd3.5 large controlnet canny")).toBe("3.5");
    expect(version("Wan 2.2 high noise")).toBe("2.2");
    expect(version("SDXL 1.0")).toBe("1.0");
    expect(version("v1.5.2 base")).toBe("1.5");
    // The whole minor: one digit of it names a version the file is not.
    expect(version("Model 1.10")).toBe("1.10");
    expect(version("Model 0.05")).toBe("0.05");
    // Past two digits it is a date or a counter, and only the major is true.
    expect(version("Flux 2.2025-10-17")).toBe("2");
    expect(version("lora_1.000002750")).toBe("1");
  });

  it("cuts any other first number to two digits", () => {
    expect(version("flux1 dev")).toBe("1");
    expect(version("Qwen Image Edit 2511")).toBe("25");
    expect(version("Juggernaut XL v9 and 10")).toBe("9");
    // A two-digit major already fills the tab, so its minor is dropped.
    expect(version("Model 12.5")).toBe("12");
  });

  it("is empty when the name carries no number, so no tab is drawn", () => {
    expect(version("World of Wordcraft")).toBe("");
  });

  it("skips a zero-padded counter, which is not a version", () => {
    // A file whose whole name is a training index reads as its raw filename,
    // which would otherwise put "00" on the tab.
    expect(generatedMark({ filename: "000002750.safetensors" }).version).toBe(
      "",
    );
    expect(version("lora 0042 v2")).toBe("2");
    expect(version("SDXL 0.9")).toBe("0.9");
  });
});

describe("stackReceipt", () => {
  it("reports the stacks that landed and the ones that did not", () => {
    // "Stacks", not "runs": a stack can span training runs - several versions
    // of one character LoRA - so calling every one a run would be false.
    expect(stackReceipt(3, 0)).toBe("Grouped 3 stacks.");
    expect(stackReceipt(2, 1)).toBe(
      "Grouped 2 stacks. 1 stack could not be grouped; something changed them first.",
    );
  });

  it("says the files are unchanged when nothing was grouped", () => {
    expect(stackReceipt(0, 2)).toBe(
      "Nothing was grouped. 2 stacks could not be, and the files are unchanged.",
    );
    expect(stackReceipt(0, 0)).toBe("Nothing to group.");
  });
});

describe("unstackReceipt", () => {
  it("says where the files went, not just that it happened", () => {
    // "Ungrouped 2 stacks." on its own is the one sentence in this view a
    // reader could reasonably fear meant "deleted", and the entire point of
    // the verb is that nothing on disk moved.
    expect(unstackReceipt(2, 0)).toBe(
      "Ungrouped 2 stacks; the files are still on the shelf.",
    );
    expect(unstackReceipt(1, 0)).toBe(
      "Ungrouped 1 stack; the files are still on the shelf.",
    );
  });

  it("counts the ones that were refused", () => {
    expect(unstackReceipt(1, 1)).toBe(
      "Ungrouped 1 stack; the files are still on the shelf. 1 stack could not be ungrouped.",
    );
    expect(unstackReceipt(0, 2)).toBe(
      "Nothing was ungrouped. 2 stacks could not be, and the files are unchanged.",
    );
    expect(unstackReceipt(0, 0)).toBe("Nothing to ungroup.");
  });
});

describe("releaseReceipt", () => {
  it("says where the files went, like the ungroup receipt does", () => {
    expect(releaseReceipt(1, 0, 0)).toBe(
      "Took 1 file out; still on the shelf.",
    );
    expect(releaseReceipt(2, 0, 0)).toBe(
      "Took 2 files out; still on the shelf.",
    );
  });

  it("names the runs that stopped existing", () => {
    // The outcome nobody asked for: taking one file out of a pair leaves a run
    // of one, which is not a run, so its last file comes loose too. A receipt
    // that said only "took 1 file out" would send the reader looking for a
    // stack that is gone.
    expect(releaseReceipt(2, 1, 0)).toBe(
      "Took 2 files out; still on the shelf. 1 run had a single file left and stopped being a run.",
    );
  });

  it("counts the ones that were refused", () => {
    expect(releaseReceipt(1, 0, 1)).toBe(
      "Took 1 file out; still on the shelf. 1 file could not be taken out.",
    );
    expect(releaseReceipt(0, 0, 2)).toBe(
      "Nothing was taken out. 2 files could not be, and the runs are unchanged.",
    );
    expect(releaseReceipt(0, 0, 0)).toBe("Nothing to take out.");
  });
});

describe("trainingStep", () => {
  it("reads the same suffix the name derivation strips", () => {
    expect(trainingStep("JimmyVehicle_000000500.safetensors")).toBe(500);
    expect(trainingStep("ohwx_woman-step00004500.safetensors")).toBe(4500);
  });

  it("reports no step for a bare final, and for a version suffix", () => {
    // `v2` is not training bookkeeping - `deriveModelName` keeps it, so this
    // must not read it as a step.
    expect(trainingStep("JimmyVehicle.safetensors")).toBe(null);
    expect(trainingStep("portrait_mix_v2.safetensors")).toBe(null);
  });
});

describe("modelVersion", () => {
  it("reads a version token past the training bookkeeping, verbatim", () => {
    expect(modelVersion("Foxglove_v2.safetensors")).toBe("v2");
    // Case preserved, matching `split_model_version`. The server puts this
    // token back into a stack's name, so folding it renames the run.
    expect(modelVersion("Foxglove_V2.1_000000500.safetensors")).toBe("V2.1");
  });

  it("agrees with the server on which versions are the SAME version", () => {
    // The divergence this pair exists to prevent: the backend compares parsed
    // `(major, minor)`, so `v2`, `V2` and `v2.0` are one version. Comparing the
    // raw tokens here made the shelf call them three, and label every member of
    // an all-v2 run with a version prefix the server never assigned.
    const key = (f) => versionSortKey(modelVersion(f));
    expect(key("Foxglove_v2.safetensors")).toBe("2.0");
    expect(key("Foxglove_V2.safetensors")).toBe("2.0");
    expect(key("Foxglove_v2.0.safetensors")).toBe("2.0");
    // And an unversioned file reads as v1, exactly as the server reads it -
    // which is what stops `Foxglove` + `Foxglove_v1` looking like two versions.
    expect(key("Foxglove.safetensors")).toBe("1.0");
    expect(key("Foxglove_v1.safetensors")).toBe("1.0");
    expect(key("Foxglove_v10.safetensors")).toBe("10.0");
  });

  it("does not read a non-ASCII digit as a version", () => {
    // Python's `\d` spans every Unicode decimal and JavaScript's does not, so
    // the backend pins `re.ASCII`. Asserted on this side too, because a silent
    // disagreement about what a version is is exactly what that pin prevents.
    expect(modelVersion("Marigold_v٢.safetensors")).toBe(null);
  });

  it("reports no version for a bare name or a bare trailing digit", () => {
    // `JimmyVehicle` beside `JimmyVehicle2` is the prefix case, which needs
    // counter-evidence; reading the `2` as a version would merge two subjects.
    expect(modelVersion("Foxglove.safetensors")).toBe(null);
    expect(modelVersion("JimmyVehicle2.safetensors")).toBe(null);
  });
});

describe("moveMechanism", () => {
  const devices = new Map([
    [1, { device_id: "d1", label: "Fast" }],
    [2, { device_id: "d1", label: "Fast" }],
    [3, { device_id: "d2", label: "Slow" }],
    [4, { device_id: null }],
  ]);
  const copies = (entries) => ({ bytesByFolderId: new Map(entries) });

  it("is a rename when every copy is on the destination's drive", () => {
    expect(moveMechanism(copies([[1, 500]]), 2, devices)).toEqual({
      how: "rename",
      freed: 0,
      drives: [],
    });
  });

  it("is a copy that frees the other drive's bytes", () => {
    expect(moveMechanism(copies([[1, 500], [3, 200]]), 2, devices)).toEqual({
      how: "copy",
      freed: 200,
      drives: ["Slow"],
    });
  });

  it("never guesses an unmeasured drive as the same one", () => {
    expect(moveMechanism(copies([[1, 500]]), 4, devices).how).toBe("unknown");
  });
});

describe("allAlreadyIn", () => {
  it("is true only when every copy sits directly in the destination", () => {
    const items = [
      { folder_id: 1, relpath: "Lora/a.st" },
      { folder_id: 1, relpath: "Lora/b.st" },
    ];
    expect(allAlreadyIn(items, { folderId: 1, path: "Lora" })).toBe(true);
    expect(allAlreadyIn(items, { folderId: 1, path: "" })).toBe(false);
    expect(
      allAlreadyIn([...items, { folder_id: 1, relpath: "c.st" }], {
        folderId: 1,
        path: "Lora",
      }),
    ).toBe(false);
  });
});

describe("where ComfyUI loads from (#2205)", () => {
  // Mixed case on purpose: every other shelf fixture is lowercase, which hides
  // a lookup that folds case on one side only.
  const reads = {
    known: true,
    reads: [
      { folder_id: 1, subpath: "Lora/Flux", category: "loras", kind: "lora" },
      { folder_id: 1, subpath: "Lora", category: "loras", kind: "lora" },
      { folder_id: 2, subpath: "", category: "checkpoints", kind: "checkpoint" },
      { folder_id: 1, subpath: "VAE", category: "vae", kind: "vae" },
    ],
  };
  const at = (folder_id, relpath) => ({ folder_id, relpath });

  it("names a copy by its path under the deepest folder that loads its kind", () => {
    expect(comfyuiName(at(1, "Lora/Flux/Neon.safetensors"), "lora", reads)).toBe(
      "Neon.safetensors",
    );
    expect(comfyuiName(at(1, "Lora/SDXL/Ink.safetensors"), "lora", reads)).toBe(
      "SDXL/Ink.safetensors",
    );
    expect(comfyuiName(at(2, "Base/Big.safetensors"), "checkpoint", reads)).toBe(
      "Base/Big.safetensors",
    );
    // The scanner stores Windows relpaths with backslashes.
    expect(comfyuiName(at(1, "Lora\\SDXL\\Ink.safetensors"), "lora", reads)).toBe(
      "SDXL/Ink.safetensors",
    );
    expect(comfyuiReadFor(at(1, "Lora/Flux/a.st"), "lora", reads).subpath).toBe(
      "Lora/Flux",
    );
  });

  it("calls a copy misplaced only outside every folder that loads its kind", () => {
    expect(isMisplaced(at(1, "Lora/Neon.safetensors"), "lora", reads)).toBe(false);
    expect(isMisplaced(at(1, "Neon.safetensors"), "lora", reads)).toBe(true);
    // A VAE folder does not load LoRAs, and a folder that merely starts with
    // the same letters is not inside it.
    expect(isMisplaced(at(1, "VAE/Neon.safetensors"), "lora", reads)).toBe(true);
    expect(isMisplaced(at(1, "LoraExtra/Neon.safetensors"), "lora", reads)).toBe(
      true,
    );
    // The disk's spelling is the one ComfyUI reported: `lora` is not `Lora`.
    expect(isMisplaced(at(1, "lora/Neon.safetensors"), "lora", reads)).toBe(true);
    expect(comfyuiName(at(1, "lora/Neon.safetensors"), "lora", reads)).toBeNull();
    // Another registered folder is another place.
    expect(isMisplaced(at(2, "Lora/Neon.safetensors"), "lora", reads)).toBe(true);
    // A file of no Type ComfyUI loads is never misplaced.
    expect(isMisplaced(at(1, "x.onnx"), "", reads)).toBe(false);
    // Nor one whose Type ComfyUI loads only from folders off the shelf: there
    // is nowhere on the shelf to drag it to.
    expect(isMisplaced(at(1, "4x.pth"), "upscaler", reads)).toBe(false);
  });

  it("puts a registered folder's own path inside ComfyUI's in front of the name", () => {
    // Registered `.../checkpoints/Flux`, ComfyUI reading `.../checkpoints`.
    const inside = {
      known: true,
      reads: [
        { folder_id: 3, subpath: "", prefix: "Flux", category: "checkpoints", kind: "checkpoint" },
      ],
    };
    expect(comfyuiName(at(3, "Dev/Big.safetensors"), "checkpoint", inside)).toBe(
      "Flux/Dev/Big.safetensors",
    );
  });

  it("guesses nothing while where ComfyUI loads from is not known", () => {
    const unknown = { known: false, reads: reads.reads };
    expect(isMisplaced(at(1, "Neon.safetensors"), "lora", unknown)).toBe(false);
    expect(isMisplaced(at(1, "Neon.safetensors"), "lora", null)).toBe(false);
    expect(comfyuiName(at(1, "Lora/Neon.safetensors"), "lora", unknown)).toBeNull();
    expect(comfyuiMarks(1, "Lora", unknown)).toEqual([]);
  });

  it("marks a folder with each Type ComfyUI loads from exactly there", () => {
    expect(comfyuiMarks(1, "Lora", reads).map((type) => type.mark)).toEqual(["L"]);
    expect(comfyuiMarks(1, "lora", reads)).toEqual([]);
    expect(comfyuiMarks(1, "", reads)).toEqual([]);
    expect(comfyuiMarks(2, "", reads).map((type) => type.key)).toEqual([
      "checkpoint",
    ]);
  });

  it("words a mark the way a sentence spells each Type", () => {
    const title = (key) =>
      comfyuiLoadsTitle(SHELF_TYPES.find((type) => type.key === key));
    expect(title("lora")).toBe("ComfyUI loads LoRAs from here");
    expect(title("text_encoder")).toBe("ComfyUI loads text encoders from here");
    expect(title("controlnet")).toBe("ComfyUI loads ControlNets from here");
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

  it("splits the weight by where the bytes are now, for the projection", () => {
    // A copy moved between two folders on one drive is a rename and adds
    // nothing to it, so the drive a drop is aimed at nets out the copies
    // already sitting on it (#894). Kept off `items`, which goes to the server
    // verbatim.
    const { items, bytesByFolderId } = movableCopies(
      [
        locRow([{ folder_id: 1, relpath: "a.st", state: "present" }], 500),
        locRow(
          [
            { folder_id: 1, relpath: "b.st", state: "present" },
            { folder_id: 4, relpath: "b.st", state: "present" },
          ],
          200,
        ),
      ],
      folders,
    );
    expect(bytesByFolderId.get(1)).toBe(700);
    expect(bytesByFolderId.get(4)).toBe(200);
    expect(items.every((item) => !("bytes" in item))).toBe(true);
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

  it("takes only the copy in the open folder (#2204)", () => {
    // A model with a second copy elsewhere is "this file" in a folder view.
    const row = locRow([
      { folder_id: 1, relpath: "Lora\\a.st", state: "present" },
      { folder_id: 1, relpath: "a.st", state: "present" },
      { folder_id: 4, relpath: "Lora/a.st", state: "present" },
    ]);
    const { items } = movableCopies([row], folders, {
      folderId: 1,
      path: "Lora",
    });
    expect(items).toEqual([{ folder_id: 1, relpath: "Lora\\a.st" }]);
    expect(
      movableCopies([row], folders, { folderId: 1, path: "" }).items,
    ).toEqual([{ folder_id: 1, relpath: "a.st" }]);
  });

  it("counts the rows it leaves behind, not their copies", () => {
    // The drag tip says how many selected models stay; a row with one movable
    // copy of two is carried, and a row with none is left.
    const { items, stays } = movableCopies(
      [
        locRow([
          { folder_id: 1, relpath: "a.st", state: "present" },
          { folder_id: 3, relpath: "a.st", state: "present" },
        ]),
        locRow([{ folder_id: 3, relpath: "b.st", state: "present" }]),
        locRow([{ folder_id: 1, relpath: "c.st", state: "missing" }]),
      ],
      folders,
    );
    expect(items).toEqual([{ folder_id: 1, relpath: "a.st" }]);
    expect(stays).toBe(2);
  });

  it("does not count a row whose copies are outside the open folder", () => {
    // In a folder view a copy elsewhere is not this file; it is not "can't
    // move". A locked copy inside the folder still is.
    const { items, stays } = movableCopies(
      [
        locRow([{ folder_id: 1, relpath: "Lora/a.st", state: "present" }]),
        locRow([{ folder_id: 1, relpath: "b.st", state: "present" }]),
        locRow([{ folder_id: 1, relpath: "c.st", state: "missing" }]),
      ],
      folders,
      { folderId: 1, path: "" },
    );
    expect(items).toEqual([{ folder_id: 1, relpath: "b.st" }]);
    expect(stays).toBe(1);
  });

  it("applies only the present rule when no folder map is given", () => {
    const { items } = movableCopies([
      locRow([{ folder_id: 2, relpath: "tagger.onnx", state: "present" }]),
    ]);
    expect(items).toHaveLength(1);
  });
});

describe("assignmentRing", () => {
  const attach = (type, id) => ({ entity_type: type, entity_id: id });

  it("takes the entity's own colour, so a character is one hue app-wide", () => {
    const ring = assignmentRing([attach("character", 7)], {
      characters: [{ id: 7, name: "Ada", character_color: "#e91e63" }],
    });
    expect(ring.hue).toBe("#e91e63");
    expect(ring.label).toBe("Ada (person)");
    expect(RING_STYLES).toContain(ring.style);
  });

  it("keys a colourless entity on its id, never on its place in the list", () => {
    // Positional colour would repaint every remaining ring when one attachment
    // is removed, which is the failure `generatedMark` documents for models.
    const lists = {
      sets: [
        { id: 4, name: "Beach" },
        { id: 9, name: "Studio" },
      ],
    };
    const alone = assignmentRing([attach("set", 9)], lists);
    const second = assignmentRing([attach("set", 9), attach("set", 4)], lists);
    expect(second.hue).toBe(alone.hue);
    expect(second.style).toBe(alone.style);
    expect(SET_COLORS.map((c) => c.value)).toContain(alone.hue);
  });

  it("gives the ring a style and a label beside its colour", () => {
    // The greyscale test, as arithmetic: strip the hue and the ring still
    // carries a treatment and a name, so nothing is said by colour alone.
    const ring = assignmentRing([attach("character", 1)], {});
    expect(RING_STYLES).toContain(ring.style);
    expect(ring.label).toBe("#1 (person)");
    expect(SET_COLORS.some((c) => c.value === ring.hue)).toBe(true);
  });

  it("holds one style per entity, whatever else the model wears", () => {
    // The style is the identity carrier, so it has to be a fact about the
    // character rather than about the row: two models sharing one assignment
    // must draw the same ring or the treatment says nothing.
    const first = assignmentRing([attach("character", 42)], {});
    const alsoOn = assignmentRing(
      [attach("character", 42), attach("set", 3)],
      {},
    );
    expect(alsoOn.style).toBe(first.style);
    expect(alsoOn.hue).toBe(first.hue);
  });

  it("names every attachment while only the first draws the ring", () => {
    const ring = assignmentRing(
      [attach("character", 1), attach("set", 2), attach("character", 3)],
      {},
    );
    expect(ring.count).toBe(3);
    expect(ring.label).toBe("#1 (person), #2 (set), #3 (person)");
  });

  it("carries the icon a set chose instead of its thumbnail", () => {
    const ring = assignmentRing([attach("set", 5)], {
      sets: [
        { id: 5, name: "Studio", set_icon: "mdi-star", set_color: "#fdd835" },
      ],
    });
    expect(ring.icon).toBe("mdi-star");
    expect(ring.hue).toBe("#fdd835");
  });

  it("carries no icon for a set still on the thumbnail", () => {
    // `cards` is the sentinel for "keep the thumbnail", so treating it as an
    // icon name would draw an mdi glyph that does not exist.
    //
    // The literal is pinned HERE, once, and the fixtures use the constant.
    // What they stand for is a value the BACKEND stored - `picture_sets.py`
    // hardcodes `!= "cards"` too, and rows written before any rename would
    // still hold the old string - so a suite built only from `ICON_CARDS`
    // would rename itself alongside the source and stay green while the two
    // sides had stopped agreeing. One pin closes that without making every
    // fixture repeat the string.
    expect(ICON_CARDS).toBe("cards");
    for (const set of [
      { id: 5, name: "Studio", set_icon: ICON_CARDS },
      { id: 5, name: "Studio" },
    ]) {
      expect(assignmentRing([attach("set", 5)], { sets: [set] }).icon).toBe("");
    }
    expect(assignmentRing([attach("character", 5)], {}).icon).toBe("");
  });

  it("draws the dashed grey ring for a model assigned to nothing", () => {
    // Never an ABSENT ring: an unringed mark under a design where every other
    // mark has one reads as a rendering gap rather than as a state.
    for (const empty of [assignmentRing([], {}), assignmentRing(undefined)]) {
      expect(empty.style).toBe("none");
      expect(empty.hue).toBe("");
      expect(empty.label).toBe("Unassigned");
      expect(empty.count).toBe(0);
    }
  });

  it("does not serve a stale name after the entity list is replaced", () => {
    // The id maps are cached per LIST REFERENCE, because this is called once
    // per shelf row and rebuilding them there cost rows x entities (#915
    // review). The entity store replaces the array on every refresh rather than
    // mutating it, so a rename must come through on the next render.
    const before = [{ id: 7, name: "Ada" }];
    const after = [{ id: 7, name: "Ada Lovelace" }];
    const label = (characters) =>
      assignmentRing([attach("character", 7)], { characters }).label;
    expect(label(before)).toBe("Ada (person)");
    expect(label(before)).toBe("Ada (person)");
    expect(label(after)).toBe("Ada Lovelace (person)");
  });

  it("picks up a set's new icon when the entity list is replaced", () => {
    // The other half of "it has to update when the icon changes": the shelf
    // reads the sets off `useEntityListsStore`, which REPLACES the array on
    // every refresh, and the id map above is cached per array reference. A
    // cache keyed on anything longer-lived would leave the mark on the face the
    // set no longer shows.
    const icon = (sets) => assignmentRing([attach("set", 5)], { sets }).icon;
    const before = [{ id: 5, name: "Studio", set_icon: ICON_CARDS }];
    const after = [{ id: 5, name: "Studio", set_icon: "mdi-star" }];
    expect(icon(before)).toBe("");
    expect(icon(after)).toBe("mdi-star");
    expect(icon(before)).toBe("");
  });

  it("takes the icon's colour from the set, never from the ring's fallback", () => {
    // `hue` invents a hashed palette entry so the ring is never invisible;
    // `iconHue` must not, or a set with no colour is drawn in theme ink by the
    // sidebar and in an arbitrary hue by the shelf, on one screen.
    const ring = assignmentRing([attach("set", 5)], {
      sets: [{ id: 5, name: "Studio", set_icon: "mdi-star" }],
    });
    expect(ring.iconHue).toBe("");
    expect(ring.hue).not.toBe("");
    expect(
      assignmentRing([attach("set", 5)], {
        sets: [{ id: 5, set_icon: "mdi-star", set_color: "#00897b" }],
      }).iconHue,
    ).toBe("#00897b");
  });
});

describe("deletableModels", () => {
  // `deletable` is the server's answer, not a kind the client re-judges: folders
  // 3 and 4 are both `foreign` and only their paths tell them apart.
  const folders = new Map([
    [1, { id: 1, kind: "user", deletable: true }],
    [2, { id: 2, kind: "managed", owner: "pixlstash", deletable: true }],
    [3, { id: 3, kind: "foreign", owner: "huggingface", deletable: false }],
    [4, { id: 4, kind: "foreign", owner: "pixlstash", deletable: true }],
  ]);

  const at = (folderId, state = "present") => ({
    file_kind: "adapter",
    locations: [{ folder_id: folderId, relpath: "a.st", state }],
  });

  it("takes a file in a folder the owner registered", () => {
    expect(deletableModels([at(1)], folders)).toHaveLength(1);
  });

  it("takes a file in the store PixlStash keeps for what it was given", () => {
    // `Add file` and an import both land there, so a shelf that could not
    // delete from it could not undo either of them.
    expect(deletableModels([at(2)], folders)).toHaveLength(1);
  });

  it("leaves the folders PixlStash keeps for itself alone", () => {
    expect(deletableModels([at(3)], folders)).toEqual([]);
  });

  it("leaves a copy on a drive that is not plugged in alone", () => {
    // "We could not look", not "it is gone". Deleting the row would orphan the
    // bytes with nothing on the shelf naming them.
    expect(deletableModels([at(1, "unreachable")], folders)).toEqual([]);
  });

  it("refuses the whole model when one of its copies is untouchable", () => {
    // Whole or not at all: unlinking the reachable half would leave the row the
    // owner wanted gone still on the shelf, rebuilt by the next scan.
    const both = {
      file_kind: "adapter",
      locations: [
        { folder_id: 1, relpath: "a.st", state: "present" },
        { folder_id: 3, relpath: "a.st", state: "present" },
      ],
    };
    expect(deletableModels([both], folders)).toEqual([]);
  });

  it("refuses an engine PixlStash downloaded for itself", () => {
    // It is declared again on every start, so the file would come straight back
    // - after whatever needed it had broken.
    const engine = { ...at(1), file_kind: "engine" };
    expect(deletableModels([engine], folders)).toEqual([]);
  });

  it("takes a leftover in the folder PixlStash downloads its engines into", () => {
    // The point of declaring them (#927): a `best.pt` beside the engines is the
    // owner's, and Forget cannot reach a file that is still there. The engines
    // in that same folder are still refused, by `file_kind` above.
    expect(deletableModels([at(4)], folders)).toHaveLength(1);
    expect(
      deletableModels([{ ...at(4), file_kind: "engine" }], folders),
    ).toEqual([]);
  });

  it("judges a stack across its members, never on its cover", () => {
    // A run is deleted whole, exactly as it is moved whole; a cover whose steps
    // sit in the HuggingFace cache is not deletable because the cover is.
    const stack = {
      file_kind: "adapter",
      locations: [{ folder_id: 1, relpath: "cover.st", state: "present" }],
      members: [at(1), at(3)],
    };
    expect(deletableModels([stack], folders)).toEqual([]);
  });

  it("deletes nothing while the folder registry is unknown", () => {
    // The safe direction to fail in, and the one Move already fails in.
    expect(deletableModels([at(1)], null)).toEqual([]);
  });
});

describe("undeletableNotice", () => {
  const folders = new Map([
    [1, { id: 1, kind: "user", path: "/models", deletable: true }],
    [
      3,
      {
        id: 3,
        kind: "foreign",
        path: "/home/u/.cache/huggingface/hub",
        movable: "fixed",
        deletable: false,
      },
    ],
    [
      5,
      {
        id: 5,
        kind: "foreign",
        path: "/home/u/.insightface",
        movable: "root_only",
        deletable: false,
      },
    ],
  ]);

  const at = (folderId, state = "present") => ({
    file_kind: "adapter",
    locations: [{ folder_id: folderId, relpath: "a.st", state }],
  });

  it("names the folder and what to do instead", () => {
    // The sentence this replaced said PixlStash "only removes files from your
    // own model folders", which is untrue - it deletes from the managed store
    // and from its own download folder - and named nothing the reader could
    // act on.
    const text = undeletableNotice([at(3)], folders);
    expect(text).toContain("/home/u/.cache/huggingface/hub");
    expect(text).toContain("Another tool owns that folder");
    expect(text).not.toContain("your own model folders");
  });

  it("does not send the reader to another tool for a folder we own", () => {
    // The InsightFace packs are PixlStash's: it re-fetches them, and there is
    // no other tool to go to.
    const text = undeletableNotice([at(5)], folders);
    expect(text).toContain("/home/u/.insightface");
    expect(text).not.toContain("Another tool owns");
  });

  it("reports every reason present, not the first one found", () => {
    // A reader who unplugs-and-replugs only to hit the folder rule has been
    // sent round twice.
    const engine = { ...at(1), file_kind: "engine" };
    const text = undeletableNotice(
      [at(3), engine, at(1, "unreachable")],
      folders,
    );
    expect(text).toContain("huggingface");
    expect(text).toContain("downloaded some of these for itself");
    expect(text).toContain("not plugged in");
  });

  it("counts the folders rather than listing six paths", () => {
    expect(undeletableNotice([at(3), at(5)], folders)).toContain(
      "2 folders PixlStash does not delete from",
    );
  });

  it("says so plainly while the folder registry is unknown", () => {
    // `deletableModels` refuses everything here, so without this the notice
    // would blame a folder rule it has not actually read.
    expect(undeletableNotice([at(1)], null)).toBe(
      "The folder list has not loaded yet, so nothing can be deleted.",
    );
  });
});

describe("trashName", () => {
  it("says Recycle Bin on Windows and Trash everywhere else", () => {
    expect(trashName({ userAgentData: { platform: "Windows" } })).toBe(
      "Recycle Bin",
    );
    expect(trashName({ userAgent: "Mozilla/5.0 (Macintosh)" })).toBe("Trash");
    // No navigator at all (a worker, an old jsdom) still names something.
    expect(trashName(null)).toBe("Trash");
  });
});

describe("companionsSentences", () => {
  const none = {
    orphaned: [],
    shared: [],
    unknown: [],
    in_use: [],
    no_evidence: [],
  };
  const named = (...names) => names.map((name, id) => ({ id, name }));

  it("says nothing when the delete affects nothing it knows of", () => {
    expect(companionsSentences(none, 2)).toBe("");
  });

  it("names every bucket, and never calls an orphan safe to delete", () => {
    const said = companionsSentences(
      {
        ...none,
        in_use: named("clip_g"),
        orphaned: named("ae", "t5xxl"),
        shared: named("clip_l"),
        unknown: named("twin"),
      },
      1,
    );
    expect(said).toContain("clip_g is still used by models you are keeping.");
    expect(said).toContain(
      "In the workflows PixlStash has read, ae and t5xxl ran only with models you are deleting; they stay on disk.",
    );
    expect(said).not.toContain("no workflow on record");
    expect(said).toContain("clip_l is still used by other models.");
    expect(said).toContain("Whether twin is still needed is unknown");
    expect(said).not.toMatch(/safe/i);
  });

  it("cuts a long list to three names and a count", () => {
    expect(
      companionsSentences(
        { ...none, orphaned: named("a", "b", "c", "d", "e") },
        1,
      ),
    ).toContain("read, a, b, c and 2 more ran only");
  });

  it("says kept models with no workflow on record may still need an orphan", () => {
    expect(
      companionsSentences({ ...none, orphaned: named("ae"), unrecorded: 3 }, 1),
    ).toContain(
      "3 other base models have no workflow on record and may still need it.",
    );
  });

  it("says when it cannot tell, rather than saying nothing", () => {
    expect(companionsSentences({ ...none, no_evidence: [3] }, 1)).toContain(
      "No workflow PixlStash has read uses this model",
    );
    expect(companionsSentences({ ...none, no_evidence: [3] }, 4)).toContain(
      "uses 1 of these models, so which support files it needs",
    );
    expect(companionsSentences(null, 2)).toContain("could not check");
  });
});

describe("the date column's value", () => {
  const ISO = "2026-07-30T12:00:00"; // naive UTC, the house convention

  it("reads a file date as st_mtime_ns and says nothing when there is none", () => {
    expect(
      modelDate(
        { newest_file_mtime: Date.UTC(2024, 0, 9, 8, 0) * 1e6 },
        "file_mtime",
      ),
    ).toBe("2024-01-09T08:00:00.000Z");
    // A row with no present copy cannot answer, and an empty cell is what says
    // so - the same thing the size column does with a size nobody recorded.
    expect(modelDate({ added_at: ISO }, "file_mtime")).toBe("");
    // A value out of `Date`'s range comes back empty rather than throwing:
    // `toISOString` throws on one, and this runs inside render, so one corrupt
    // mtime would take the whole shelf down rather than one cell.
    expect(modelDate({ newest_file_mtime: 1e30 }, "file_mtime")).toBe("");
    expect(modelDate({ newest_file_mtime: "not a number" }, "file_mtime")).toBe(
      "",
    );
  });

  it("falls back to Date added on every key that is not a date", () => {
    expect(dateColumnKey("size")).toBe("added_at");
    expect(dateColumnKey("file_mtime")).toBe("file_mtime");
    expect(modelDate({ added_at: ISO }, "size")).toBe(ISO);
    // A stack's date is its newest member's, never its cover's - and a member
    // read for its own is read for its own.
    const member = { added_at: ISO, newest_member_at: "2026-09-01T00:00:00" };
    expect(modelDate(member, "added_at")).toBe("2026-09-01T00:00:00");
    expect(modelDate(member, "added_at", true)).toBe(ISO);
  });
});

describe("a key from the Folders tree (#2206)", () => {
  it("is the tree's when it is pressed on a tree row, and not the shelf's", () => {
    const tree = document.createElement("div");
    tree.className = "mft";
    const row = document.createElement("div");
    tree.appendChild(row);
    const shelf = document.createElement("div");
    shelf.className = "shelf-row";
    expect(isInFoldersTree({ target: row })).toBe(true);
    expect(isInFoldersTree({ target: shelf })).toBe(false);
    // A press with nothing focused lands on the body, which is nobody's tree.
    expect(isInFoldersTree({ target: document.body })).toBe(false);
    expect(isInFoldersTree({})).toBe(false);
  });
});

describe("where a dragged model belongs", () => {
  const sm = { id: 1, path: "/home/me/StabilityMatrix/Data/Models", tool: "StabilityMatrix" };
  const comfy = { id: 2, path: "D:\\ComfyUI\\models" };
  const plain = { id: 3, path: "/home/me/models" };
  const listing = (dirs, direct = 0) => ({
    listing: { direct_model_count: direct, dirs },
  });
  const dir = (path, direct) => ({ name: path.split("/").pop(), path, direct_model_count: direct });

  it("reads a known tool's layout straight under its models folder", () => {
    expect(layoutKind(sm, "DiffusionModels")).toBe("checkpoint");
    expect(layoutKind(sm, "LyCORIS")).toBe("lora");
    expect(layoutKind(sm, "ESRGAN")).toBe("upscaler");
    expect(layoutKind(comfy, "diffusion_models")).toBe("checkpoint");
    expect(layoutKind(comfy, "clip")).toBe("text_encoder");
    // A folder registered at the Type folder itself.
    expect(layoutKind({ ...sm, path: `${sm.path}/Lora` }, "")).toBe("lora");
    // A Models folder moved behind a symlink is stored at its resolved path,
    // named for wherever it went.
    const moved = { ...sm, path: "/home/me/.vault/Models/StabilityMatrix" };
    expect(layoutKind(moved, "Lora")).toBe("lora");
    expect(layoutKind(moved, "Lora/flux")).toBe("");
    // A registered Type folder's own subfolders are the owner's sorting.
    expect(layoutKind({ ...sm, path: `${sm.path}/Lora` }, "vae")).toBe("");
    // Deeper is the owner's own sorting, and no tool is never guessed.
    expect(layoutKind(sm, "Lora/vae")).toBe("");
    expect(layoutKind(sm, "Embeddings")).toBe("");
    expect(layoutKind(plain, "loras")).toBe("");
  });

  it("asks ComfyUI first, and the layout only for a folder its reads miss", () => {
    const listings = new Map([
      ["1:", listing([dir("Lora", 40), dir("DiffusionModels", 6), dir("Embeddings", 2)], 3)],
      ["2:", listing([dir("loras", 9), dir("unet", 1)])],
    ]);
    const reads = {
      known: true,
      reads: [{ folder_id: 2, subpath: "loras", kind: "lora" }],
    };
    expect(modelDestinations([sm, comfy], listings, reads)).toEqual([
      { folderId: 1, path: "Lora", kind: "lora", count: 40 },
      { folderId: 1, path: "DiffusionModels", kind: "checkpoint", count: 6 },
      // ComfyUI does not load from unet here, so the layout is not asked.
      { folderId: 2, path: "loras", kind: "lora", count: 9 },
    ]);
    expect(modelDestinations([comfy], listings, { known: false, reads: [] })).toEqual([
      { folderId: 2, path: "loras", kind: "lora", count: 9 },
      { folderId: 2, path: "unet", kind: "checkpoint", count: 1 },
    ]);
  });

  it("puts a Type on a second line only where the name does not say it", () => {
    const words = (name, ...kinds) =>
      unsaidTypes(name, new Set(kinds)).map((type) => type.label);
    expect(words("DiffusionModels", "checkpoint")).toEqual(["Checkpoints"]);
    expect(words("CLIP", "text_encoder")).toEqual(["Text encoders"]);
    expect(words("LyCORIS", "lora")).toEqual(["LoRAs"]);
    expect(words("Lora", "lora")).toEqual([]);
    expect(words("text_encoders", "text_encoder")).toEqual([]);
    expect(words("upscale_models", "upscaler")).toEqual([]);
  });
});
