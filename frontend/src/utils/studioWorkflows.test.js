import { describe, expect, it } from "vitest";
import { isSceneClipCandidate, isSceneStillWorkflow } from "./studioWorkflows";

const card = (over) => ({ name: "Some workflow", type: "img2img", reference_inputs: 1, ...over });

describe("the workflows a scene offers", () => {
  it("offers an image workflow that takes reference pictures", () => {
    expect(isSceneStillWorkflow(card())).toBe(true);
    expect(isSceneStillWorkflow(card({ type: "txt2img", reference_inputs: 2 }))).toBe(true);
  });

  it("offers none that takes no reference, makes no picture, or has a job elsewhere", () => {
    expect(isSceneStillWorkflow(card({ reference_inputs: 0 }))).toBe(false);
    for (const type of ["inpaint", "outpaint", "upscale", "video", null]) {
      expect(isSceneStillWorkflow(card({ type })), String(type)).toBe(false);
    }
    expect(isSceneStillWorkflow(card({ name: "FLUX.2 [Klein] 9B: Image Edit (sheet)" }))).toBe(false);
    expect(isSceneStillWorkflow(card({ sheet_layout: true }))).toBe(false);
    expect(isSceneStillWorkflow(card({ hidden: true }))).toBe(false);
    expect(isSceneStillWorkflow(card({ draft: true }))).toBe(false);
    expect(isSceneStillWorkflow(card({ name: "MiniMax H3 Restore a Frame (PixlStash)" }))).toBe(false);
  });

  it("weighs a video workflow for a clip, never the location shots", () => {
    expect(isSceneClipCandidate(card({ type: "video", reference_inputs: 0 }))).toBe(true);
    expect(isSceneClipCandidate(card({ type: "img2img" }))).toBe(false);
    expect(
      isSceneClipCandidate(card({ type: "video", name: "MiniMax H3 Location Shots (PixlStash)" })),
    ).toBe(false);
  });
});
