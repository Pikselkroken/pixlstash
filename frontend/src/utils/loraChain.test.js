import { describe, it, expect } from "vitest";

import {
  chainEntries,
  countChanges,
  editLorasRoute,
  loraBase,
  loraStem,
  movesBetween,
} from "./loraChain";

describe("loraChain", () => {
  it("names a file by its stem, and compares it case-folded without a folder", () => {
    expect(loraStem("sub/Hairstyle-V3.safetensors")).toBe("Hairstyle-V3");
    expect(loraStem("sub\\no-extension")).toBe("no-extension");
    expect(loraBase("loras/Hairstyle-V3.safetensors")).toBe(
      "hairstyle-v3.safetensors",
    );
  });

  it("counts a neighbour swap as one move, not two", () => {
    expect(movesBetween(["a", "b", "c", "d"], ["a", "c", "b", "d"])).toBe(1);
    expect(movesBetween(["a", "b", "c"], ["c", "b", "a"])).toBe(2);
    // A delete and an add are not moves.
    expect(movesBetween(["a", "b", "c"], ["a", "c", "x"])).toBe(0);
  });

  it("sends a standing row by node id and a new one by digest", () => {
    const rows = [
      { nodeId: "14", strength: 1, deleted: false, isNew: false },
      { nodeId: "33", strength: 0.6, deleted: true, isNew: false },
      { nodeId: "22", strength: null, deleted: false, isNew: false },
      { nodeId: null, sha256: "d", strength: 0.3, deleted: false, isNew: true },
    ];
    expect(chainEntries(rows)).toEqual([
      { node_id: "14", strength: 1 },
      // A wired strength is not written back as a number.
      { node_id: "22" },
      { node_id: null, sha256: "d", strength: 0.3 },
    ]);
    expect(
      countChanges(
        [
          { node_id: "14", strength: 1 },
          { node_id: "22", strength: null },
          { node_id: "33", strength: 0.6 },
        ],
        rows,
      ),
    ).toBe(2);
  });

  it("builds the hand-over link Save as recipe follows", () => {
    expect(editLorasRoute("k", { dropLora: "x.safetensors" })).toEqual({
      name: "workflows",
      query: { card: "k", edit: "loras", drop_lora: "x.safetensors" },
    });
    expect(editLorasRoute("k")).toEqual({
      name: "workflows",
      query: { card: "k", edit: "loras" },
    });
  });
});
