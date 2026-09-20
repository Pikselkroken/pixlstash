// Why a run would not happen, in the owner's words, with what to do about it.
//
// `POST /workflows/run/preflight` answers in codes and payloads rather than in
// prose (`pixlstash/services/workflow_run_service.py`), precisely so that one
// batch mixing several cards can be grouped and acted on. This is the one place
// that turns a code into a sentence, shared by the Run popup and the Make more
// popup so the two never word the same refusal differently.
//
// `fix` names an action the popup offers; `null` means the sentence is all
// there is to say and a help link stands beside it.
//
// Not everything drawn here is a refusal: `blocking: false` is a fact about the
// run that IS about to happen (a bypassed LoRA, #1463), which the same notice
// draws in the warning hue and without the "can't run" lead.

/** ComfyUI never answered, so nothing at all is known about the graph. */
export const UNCHECKED_CODES = ["comfyui_unreachable", "comfyui_not_configured"];

/** Installing a file is a trip away from the keyboard, so it stops the batch. */
export const BLOCKS_BATCH = "missing_models";

/**
 * Not a refusal: a LoRA loader the server took out of the graph (#1463).
 *
 * A LoRA is optional, so a missing one is bypassed and the run happens without
 * it, where a missing checkpoint still blocks. It has no reason code of its own
 * because it is not a reason — `RunGroup.bypassed_loras` carries it, beside
 * `substitutions`. `bypassNotice` turns it into the reason shape so it can be
 * drawn by the same notice the refusals use, in the one place the owner is
 * already reading why this run is not quite what the card says.
 */
export const LORAS_BYPASSED = "loras_bypassed";

/** One value per `fix`, so a caller switches on a constant and not on prose. */
export const FIX_SETTINGS = "settings";
export const FIX_RETRY = "retry";
export const FIX_DROP_LORA = "drop-lora";

/**
 * Codes whose fix happens somewhere ELSE, so the popup has to be able to
 * re-ask once it has been done.
 *
 * Settings opens on top of this popup and nothing tells the popup when it
 * closes, so without a Retry of its own `comfyui_not_configured` stayed on
 * screen — and the Run button stayed blocked — after the address had been set,
 * with closing and reopening (and losing the form) the only way out.
 */
export const RETRYABLE_CODES = ["comfyui_not_configured", "comfyui_unreachable"];

function names(list, key) {
  return (list || [])
    .map((item) => (typeof item === "string" ? item : item?.[key]))
    .filter(Boolean);
}

/**
 * One refusal, read - or one notice, when `blocking` comes back false.
 *
 * @param {{code: string}} reason - a group's reason, code plus its payload.
 * @returns {{code: string, text: string, fix: ?string, files: Array<Object>,
 *   blocking: boolean, retry: boolean}}
 */
export function readReason(reason) {
  const code = String(reason?.code || "");
  const read = (text, fix = null, files = [], blocking = true) => ({
    code,
    text,
    fix,
    files,
    blocking,
    retry: RETRYABLE_CODES.includes(code),
  });
  switch (code) {
    case LORAS_BYPASSED: {
      const files = (reason.models || []).filter(Boolean);
      const count = files.length;
      return read(
        `${count === 1 ? "A LoRA this workflow uses is" : `${count} LoRAs this workflow uses are`} not on this ComfyUI, so ${count === 1 ? "its loader is" : "their loaders are"} skipped and the run goes ahead without ${count === 1 ? "it" : "them"}. The result will look different.`,
        null,
        files,
        false,
      );
    }
    case "comfyui_not_configured":
      return read(
        "PixlStash has no ComfyUI address to run this on.",
        FIX_SETTINGS,
      );
    case "comfyui_unreachable":
      return read("ComfyUI did not answer, so its graph could not be checked.", FIX_RETRY);
    case "missing_models": {
      const files = (reason.models || []).filter(Boolean);
      const count = files.length;
      return read(
        `${count} ${count === 1 ? "model is" : "models are"} missing.`,
        null,
        files,
      );
    }
    case "missing_nodes": {
      const nodes = names(reason.nodes, "name");
      return read(
        `This ComfyUI does not have ${nodes.length === 1 ? "the node" : "the nodes"} ${nodes.join(", ")}.`,
      );
    }
    case "no_lora_loader":
      // The reason only ever fires because the run is PUTTING a LoRA in, so
      // taking it out again is the fix this route can actually carry out.
      // Inserting a loader is the shipped replay route's trick (#1376) and
      // `POST /workflows/run` has no field for it.
      return read(
        "This workflow has no LoRA loader, so the LoRA has nowhere to go.",
        FIX_DROP_LORA,
      );
    case "pixlstash_nodes":
      return read("This graph calls back into PixlStash, so PixlStash will not run it.");
    case "no_save_node":
      return read("This graph saves no image, so a run would produce nothing to keep.");
    case "a1111":
      return read("This picture was made in A1111 or Forge, so there is no ComfyUI graph to run.");
    case "ui_format":
      return read(
        "The workflow file this card names is an editor export, which ComfyUI cannot be handed.",
      );
    case "fixed_input_deleted": {
      const slots = names(reason.inputs, "slot_label");
      // No fix offered: replacing one input means PUTting the card's WHOLE
      // input set, and nothing reads the set back, so a fix here would delete
      // every row it could not see.
      return read(
        `A picture this workflow always loads is gone (${slots.join(", ") || "one input"}). Set it again on the workflow's own tab.`,
      );
    }
    case "no_runnable_source":
      return read("Nothing on this machine can produce a graph for this workflow.");
    default:
      return read("This workflow cannot be run just now.");
  }
}

/**
 * Whether the reasons left on a group still stop it running.
 *
 * Mirrors `workflow_run_service.blocks_group`: an uninspectable ComfyUI is the
 * only thing consent clears, because it is the only one where nothing was
 * established. A missing model is a fact, and there is nothing to consent to.
 *
 * @param {Array<Object>} reasons
 * @param {{allowUnchecked?: boolean}} [options]
 * @returns {boolean}
 */
export function reasonsBlock(reasons, { allowUnchecked = false } = {}) {
  return (reasons || []).some((reason) => {
    const code = String(reason?.code || "");
    return !(allowUnchecked && UNCHECKED_CODES.includes(code));
  });
}

/**
 * The bypassed LoRAs of one pre-flight group, in the reason shape.
 *
 * Kept OUT of `reasons` on purpose: `reasons` empty is the one thing that means
 * "this would run", on both sides of the wire, and a notice that is not a
 * refusal must not make a runnable group look blocked. This only reaches the
 * list a popup draws.
 *
 * @param {{bypassed_loras?: Array<Object>}} group
 * @returns {Array<Object>} zero or one entry, so a caller can spread it.
 */
export function bypassNotice(group) {
  const models = (group?.bypassed_loras || []).filter(Boolean);
  return models.length ? [{ code: LORAS_BYPASSED, models }] : [];
}
