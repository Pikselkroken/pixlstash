// runTaskLabel.js - what a ComfyUI run's row in the Tasks tab is called.

/** The row's name when a run says nothing about itself. */
export const RUN_TASK_FALLBACK = "ComfyUI";

/**
 * The first that applies: a clone of a picture (its own recipe, untouched,
 * with its own seed), the saved recipe being run, the workflow being run.
 *
 * @param {{clone?: boolean, recipe?: string, workflow?: string}} run
 * @returns {string}
 */
export function runTaskLabel({ clone = false, recipe = "", workflow = "" } = {}) {
  if (clone) return "Clone picture";
  return (
    String(recipe || "").trim() ||
    String(workflow || "").trim() ||
    RUN_TASK_FALLBACK
  );
}

/** The prompts a run answered with, each carrying the row name it runs under. */
export function labelPrompts(prompts, labelOf) {
  return prompts.map((prompt) => ({ ...prompt, label: labelOf(prompt) }));
}
