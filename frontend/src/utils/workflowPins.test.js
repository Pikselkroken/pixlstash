// The drop-down options of a workflow's parameters, by address.

import { describe, it, expect } from "vitest";

import { choicesFor, optionsByAddress } from "./workflowPins";

const NODES = [
  {
    node_id: "3",
    inputs: [
      { slot_label: "core:a", input_name: "sampler_name", options: ["euler", "dpmpp_2m"] },
      { slot_label: "core:a", input_name: "steps", options: null },
    ],
  },
];

describe("a parameter's choices", () => {
  it("are listed for a drop-down and for nothing else", () => {
    const options = optionsByAddress(NODES);
    expect(options).toEqual({ "core:a/sampler_name": ["euler", "dpmpp_2m"] });
    const row = (input_name, value) => ({ slot_label: "core:a", input_name, value });
    expect(choicesFor(options, row("sampler_name", "euler"))).toEqual([
      "euler",
      "dpmpp_2m",
    ]);
    expect(choicesFor(options, row("steps", 8))).toBeNull();
    // The same input name on another node is another parameter.
    expect(
      choicesFor(options, { slot_label: "core:b", input_name: "sampler_name", value: "x" }),
    ).toBeNull();
  });

  it("keep a value this ComfyUI no longer lists, so the row still says it", () => {
    const options = optionsByAddress(NODES);
    const row = { slot_label: "core:a", input_name: "sampler_name", value: "res_2s" };
    expect(choicesFor(options, row)).toEqual(["res_2s", "euler", "dpmpp_2m"]);
    // The Run form asks about the value it shows, not the one it started at.
    expect(choicesFor(options, row, "euler")).toEqual(["euler", "dpmpp_2m"]);
  });
});
