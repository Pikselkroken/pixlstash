// The drop-down options of a workflow's parameters, by address.

import { describe, it, expect } from "vitest";

import {
  choicesFor,
  nodeNames,
  nodesByAddress,
  optionsByAddress,
} from "./workflowPins";

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

  it("are on and off for a value that is one of the two, listed or not", () => {
    const row = { slot_label: "core:z", input_name: "tiled", value: false };
    expect(choicesFor({}, row)).toEqual(["true", "false"]);
    expect(choicesFor(null, row, true)).toEqual(["true", "false"]);
  });

  it("keep a value this ComfyUI no longer lists, so the row still says it", () => {
    const options = optionsByAddress(NODES);
    const row = { slot_label: "core:a", input_name: "sampler_name", value: "res_2s" };
    expect(choicesFor(options, row)).toEqual(["res_2s", "euler", "dpmpp_2m"]);
    // The Run form asks about the value it shows, not the one it started at.
    expect(choicesFor(options, row, "euler")).toEqual(["euler", "dpmpp_2m"]);
  });
});

describe("a parameter's node", () => {
  const twins = [
    { node_id: "6", title: "Prompt", inputs: [{ slot_label: "a", input_name: "text" }] },
    { node_id: "7", title: "Prompt", inputs: [{ slot_label: "b", input_name: "text" }] },
    { node_id: "8", title: "Upscale", inputs: [{ slot_label: "c", input_name: "scale_by" }] },
  ];

  it("is named by its title, and by its number too where two share one", () => {
    expect(nodeNames(twins)).toEqual({ 6: "Prompt #6", 7: "Prompt #7", 8: "Upscale" });
    expect(nodesByAddress(twins)).toEqual({
      "a/text": "Prompt #6",
      "b/text": "Prompt #7",
      "c/scale_by": "Upscale",
    });
  });
});
