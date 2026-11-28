/**
 * Which workflows a scene's Shoot offers (#2819): the ones that can render a
 * scene, not every workflow in the library.
 *
 * A still is composed from the scene's pictures (the location's view, the
 * cast's references, outfits, props), so it takes reference pictures and
 * makes a new picture of them: an image workflow with at least one reference
 * input. A clip is made from a still take, so a video workflow that takes a
 * picture (decided by its pre-flight, since a card cannot say). Never offered:
 * a hidden workflow, a draft that cannot run yet, a sheet workflow, or one of
 * the Studio's own pipeline steps, each made for one job elsewhere.
 */
export const STUDIO_PIPELINE_WORKFLOWS = new Set([
  "MiniMax H3 Location Shots (PixlStash)",
  "MiniMax H3 Location Shots, First and Last (PixlStash)",
  "MiniMax H3 Restore a Frame (PixlStash)",
  "MiniMax H3 Image Sheet (PixlStash)",
  "MiniMax H3 Image Sheet from Reference (PixlStash)",
  "MiniMax H3 Image Inpaint (PixlStash)",
  "MiniMax H3 Image T2I (PixlStash)",
]);

const IMAGE_TYPES = new Set(["img2img", "txt2img"]);

/** One of the built-in scene workflows, made to take every picture a scene
 * sends (its picture input fans out), so Shoot offers it first. */
export function isSceneWorkflow(card) {
  return !!card?.built_in && /\bScene \(PixlStash\)$/.test(card.name || "");
}

/** Whether *card* may be offered at all, whatever it makes. */
function offerable(card) {
  return (
    !!card &&
    !card.hidden &&
    !card.draft &&
    !card.sheet_layout &&
    !/\bsheet\b/i.test(card.name || "") &&
    !STUDIO_PIPELINE_WORKFLOWS.has(card.name)
  );
}

/** A workflow a scene's still can be shot with. */
export function isSceneStillWorkflow(card) {
  return offerable(card) && IMAGE_TYPES.has(card.type) && (card.reference_inputs || 0) > 0;
}

/** A video workflow that may make a scene's clip, before its pre-flight
 * says whether it takes the still. */
export function isSceneClipCandidate(card) {
  return offerable(card) && card.type === "video";
}
