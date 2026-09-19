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

/** ComfyUI never answered, so nothing at all is known about the graph. */
export const UNCHECKED_CODES = ["comfyui_unreachable", "comfyui_not_configured"];

/** Installing a file is a trip away from the keyboard, so it stops the batch. */
export const BLOCKS_BATCH = "missing_models";

/** One value per `fix`, so a caller switches on a constant and not on prose. */
export const FIX_SETTINGS = "settings";
export const FIX_RETRY = "retry";
export const FIX_DROP_LORA = "drop-lora";

function names(list, key) {
  return (list || [])
    .map((item) => (typeof item === "string" ? item : item?.[key]))
    .filter(Boolean);
}

/**
 * One refusal, read.
 *
 * @param {{code: string}} reason - a group's reason, code plus its payload.
 * @returns {{code: string, text: string, fix: ?string, files: Array<Object>}}
 */
export function readReason(reason) {
  const code = String(reason?.code || "");
  const read = (text, fix = null, files = []) => ({ code, text, fix, files });
  switch (code) {
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
