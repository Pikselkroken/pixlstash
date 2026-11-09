/**
 * The recipe read (`GET /comfyui/pictures/{id}/recipe`) in the shape the
 * Recipe tab (`LightboxRecipePanel`) reads, or null for a picture that has no
 * recipe. Shared by the lightbox's inspector and the grid's.
 *
 * `reason: "no_prompt_chunk"` is the read's honest "this picture was not
 * generated", not an error: it answers 200, so the tab's absence comes from
 * here rather than from a 404.
 *
 * @param {Object|null} data the read's body.
 * @returns {Object|null}
 */
export function recipeForPanel(data) {
  if (!data || data.reason === "no_prompt_chunk") return null;
  return {
    source: data.source || "comfyui",
    // Whether this recipe can be REPLAYED, which is not the same as whether it
    // can be read: an A1111 picture answers here in full and is still not a
    // graph ComfyUI could be handed.
    available: data.available,
    reason: data.reason || null,
    summary: data.summary,
    positive_prompt: data.positive_prompt || null,
    negativePrompt: data.negative_prompt || null,
    // The seed as text: a 64-bit one loses digits as a JS number.
    seedText: data.seed_text || null,
    // `model_slots` carries the strengths and the shelf rows that the flat
    // `models` name list cannot; `inputs` is the resolution lock.
    modelSlots: data.model_slots || [],
    settings: data.settings || {},
    inputs: data.inputs || [],
    topologyHash: data.topology_hash || null,
    // Why the editor graph could not be rebuilt into something runnable.
    // Empty for every other answer.
    conversionProblems: data.conversion_problems || [],
    // The workflow this picture's variant is in. The Recipe tab needs it to
    // read the workflow's saved recipes and to file a new one under it (v1.12
    // F6); null when the hub has not keyed this picture.
    workflowId: data.workflow_id || null,
    // The plain file names, which is the form saved-recipe credit matches on -
    // `model_slots` carries strengths the match ignores.
    loraNames: data.loras || [],
  };
}
