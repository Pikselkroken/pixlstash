// The one place a pre-flight code becomes a sentence (`runReasons.js`). Its
// other branches are covered through the popups that draw them; this file
// holds the ones whose wording carries a contract of its own.

import { describe, it, expect } from "vitest";

import {
  PICTURE_INPUT_UNFILLED,
  PIXLSTASH_PACK_INSTALL,
  readReason,
  reasonsBlock,
  unplacedNotice,
} from "./runReasons";

describe("picture_input_unfilled (#1457)", () => {
  const reason = {
    code: PICTURE_INPUT_UNFILLED,
    inputs: [
      { slot_label: "9f".repeat(32), input_name: "image", title: "Reference" },
    ],
  };

  it("names the input by its title, never by the slot label hash", () => {
    const read = readReason(reason);
    expect(read.text).toContain("Reference");
    expect(read.text).not.toContain("9f9f");
    expect(read.blocking).toBe(true);
  });

  it("still reads when an older server sent no title", () => {
    const read = readReason({
      code: PICTURE_INPUT_UNFILLED,
      inputs: [{ slot_label: "9f".repeat(32), input_name: "image" }],
    });
    expect(read.text).toContain("one of its picture inputs");
    expect(read.text).not.toContain("9f9f");
  });

  it("blocks, as every refusal does", () => {
    expect(reasonsBlock([reason])).toBe(true);
  });

  it("is not read as the retired fixed_input_deleted", () => {
    // A client pinned to the old code falls back to the generic sentence; the
    // old one's "Set it again on the workflow's own tab" pointed nowhere.
    expect(readReason({ code: "fixed_input_deleted" }).text).toBe(
      "This workflow cannot be run just now.",
    );
  });
});

describe("pixlstash_nodes (#1521)", () => {
  it("names each refused node and why", () => {
    const read = readReason({
      code: "pixlstash_nodes",
      nodes: [
        { node_id: "10", title: "Holiday", why: "not_in_library", kind: "project", id: 9 },
        { node_id: "12", title: "Subject", why: "picks_its_own_picture" },
      ],
    });
    expect(read.text).toContain("Holiday names a project this library does not have");
    expect(read.text).toContain("Subject would pick its own pictures");
    expect(read.blocking).toBe(true);
  });

  it("keeps the old sentence for a server that names no nodes", () => {
    expect(readReason({ code: "pixlstash_nodes" }).text).toBe(
      "This graph calls back into PixlStash, so PixlStash will not run it.",
    );
  });
});

describe("missing_nodes names our own pack", () => {
  it("says a PixlStash node comes from the pack, and how to install it", () => {
    const read = readReason({
      code: "missing_nodes",
      nodes: [{ name: "PixlStashCheckpointLoader" }],
    });
    expect(read.text).toBe(
      "PixlStashCheckpointLoader comes from the ComfyUI-PixlStash node pack, " +
        "which this ComfyUI does not have. " +
        PIXLSTASH_PACK_INSTALL,
    );
    expect(read.text).toContain("github.com/Pikselkroken/ComfyUI-PixlStash");
  });

  it("says both sentences for a mixed list", () => {
    const read = readReason({
      code: "missing_nodes",
      nodes: [{ name: "rgthreeSeed" }, { name: "PixlStashAdapterLoader" }],
    });
    expect(read.text).toBe(
      "This ComfyUI does not have the node rgthreeSeed. " +
        "PixlStashAdapterLoader comes from the ComfyUI-PixlStash node pack, " +
        "which this ComfyUI does not have. " +
        PIXLSTASH_PACK_INSTALL,
    );
  });

  it("leaves a third-party node's sentence as it was", () => {
    const read = readReason({
      code: "missing_nodes",
      nodes: [{ name: "rgthreeSeed" }, { name: "KSamplerAdvanced" }],
    });
    expect(read.text).toBe(
      "This ComfyUI does not have the nodes rgthreeSeed, KSamplerAdvanced.",
    );
    expect(read.text).not.toContain("PixlStash");
  });
});

describe("loras_unplaced names its workflow by id (#1623)", () => {
  it("carries the group's workflow_id for Edit LoRAs…", () => {
    const [notice] = unplacedNotice({
      workflow_id: "auto:" + "c".repeat(64),
      unplaced_loras: [{ filename: "a.safetensors" }],
    });
    expect(notice.workflowId).toBe("auto:" + "c".repeat(64));
    expect(notice).not.toHaveProperty("workflowKey");
  });
});

describe("loras_fetched", () => {
  it("names the files the digest loader fetches, and is not a refusal", () => {
    const read = readReason({
      code: "loras_fetched",
      loaders: [{ file: "loras/example-subject.safetensors" }],
    });
    expect(read.text).toBe(
      "example-subject.safetensors is not on this ComfyUI, so it loads through the ComfyUI-PixlStash LoRA loader, which fetches it by hash.",
    );
    expect(read.blocking).toBe(false);
  });

  it("still reads as a sentence when no file is named", () => {
    const read = readReason({ code: "loras_fetched", loaders: [{}] });
    expect(read.text).toMatch(/^A LoRA this ComfyUI does not have loads through/);
  });
});

describe("ui_format", () => {
  it("includes the detail when provided", () => {
    const read = readReason({
      code: "ui_format",
      detail:
        "TextGenerate (node 30:16) has 0.7 where its 'use_default_template' input takes a BOOLEAN, so its widget values do not line up with the node this ComfyUI has.",
    });
    expect(read.text).toContain(
      "PixlStash could not turn this ComfyUI workflow into one it can run:",
    );
    expect(read.text).toContain("TextGenerate (node 30:16)");
    expect(read.text).toContain(
      "Open it in ComfyUI from here and PixlStash converts it there.",
    );
    expect(read.blocking).toBe(true);
  });

  it("still reads when no detail is provided", () => {
    const read = readReason({ code: "ui_format" });
    expect(read.text).toBe(
      "PixlStash could not turn this ComfyUI workflow into one it can run. Open it in ComfyUI from here and PixlStash converts it there.",
    );
    expect(read.blocking).toBe(true);
  });
});
