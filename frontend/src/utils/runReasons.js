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

/**
 * Not a refusal: a LoRA loader the OWNER skipped for this run.
 *
 * The same `bypassed_loras` list carries these, with `requested: true`, beside
 * the ones the server bypassed on its own (`requested: false`, a file this
 * ComfyUI does not have). They are said differently because they ARE
 * different: one is a fact to be warned about, the other is the owner's own
 * choice read back, in the popup's word for it — skipped, never removed,
 * because a run changes no workflow.
 */
export const LORAS_SKIPPED = "loras_skipped";

/**
 * Not a refusal either: a recipe LoRA that found no loader to go in (#1478).
 *
 * A saved recipe's LoRAs are matched onto the graph's loaders by digest, then
 * by basename, then onto the free loaders left in order; one with nowhere left
 * to go — a three-LoRA recipe on a two-loader member of its stack, or a LoRA
 * the shelf cannot identify at all — is reported on `RunGroup.unplaced_loras`
 * rather than dropped in silence. The run still goes ahead, so it is drawn in
 * the warning hue with Edit LoRAs… as its fix: adding a loader is a workflow
 * edit, and that dialog is where it is made.
 */
export const LORAS_UNPLACED = "loras_unplaced";

/**
 * A picture input nothing answered (#1457; it replaced `fixed_input_deleted`).
 *
 * The Run popup draws it in its Pictures section, as an empty slot, and not as
 * a refusal notice: decision 7 of the issue is that a pin whose picture has
 * gone is "no picture yet", not an error.
 */
export const PICTURE_INPUT_UNFILLED = "picture_input_unfilled";

/**
 * Not a refusal either: a custom node the server replaced (#1463).
 *
 * Today only a seed node (rgthree's `Seed (rgthree)` and its kin) this ComfyUI
 * lacks: its link becomes a literal and the run's own seed pass writes it, so
 * the run goes ahead without the pack. `RunGroup.replaced_nodes` carries it.
 */
export const NODES_REPLACED = "nodes_replaced";

/** One value per `fix`, so a caller switches on a constant and not on prose. */
export const FIX_SETTINGS = "settings";
export const FIX_RETRY = "retry";
export const FIX_DROP_LORA = "drop-lora";
export const FIX_EDIT_LORAS = "edit-loras";

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

/** Why one ComfyUI-PixlStash node may not run, as the pre-flight said. */
function pixlstashNodeSentence(node) {
  const title = node.title || node.class_type || "A PixlStash node";
  switch (node.why) {
    case "not_in_library":
      return `${title} names a ${node.kind} this library does not have. Choose one in ComfyUI.`;
    case "unreadable_id":
      return `${title} takes its ${node.kind} from another node, so PixlStash cannot check it is in this library.`;
    case "picks_its_own_picture":
      return `${title} would pick its own pictures, because this run gives it none.`;
    case "per_hub_checkpoint":
      return `${title} names a checkpoint on another machine's shelf. It runs only from the stored workflow file.`;
    default:
      return `${title} is a PixlStash node this version does not know how to run.`;
  }
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
    case NODES_REPLACED: {
      // Named by class, once each, in the sentence: a node is not a file, and
      // the files list would draw it as one.
      const nodes = [...new Set(names(reason.nodes, "class_type"))];
      const one = nodes.length === 1;
      return read(
        `This ComfyUI does not have ${one ? "the seed node" : "the seed nodes"} ${nodes.join(", ")}, so PixlStash writes the seed straight into the sampler instead. The run goes ahead without ${one ? "it" : "them"}.`,
        null,
        [],
        false,
      );
    }
    case LORAS_SKIPPED: {
      const files = (reason.models || []).filter(Boolean);
      return read(
        `Skipped for this run: ${files.map((model) => model.file).filter(Boolean).join(", ")}.`,
        null,
        [],
        false,
      );
    }
    case "lora_not_skippable": {
      // Blocking: a stacker whose other LoRAs are present, or a loader nothing
      // can be rewired around. The server's own sentence says which.
      const file = String(reason.file || "").split(/[\\/]/).pop();
      const said = String(reason.text || reason.detail || reason.message || "").trim();
      return read(
        `${file || "This LoRA"} cannot be skipped for this run. ${said || "Its loader cannot be taken out of this graph."} Use it, or edit the workflow's LoRAs.`,
      );
    }
    case LORAS_UNPLACED: {
      // Shaped like the bypass's `models` so the notice's file list draws
      // them: the file, then the server's own reason for it.
      const loras = (reason.loras || []).filter(Boolean);
      const files = loras.map((lora) => ({
        file: String(lora.filename || "").split(/[\\/]/).pop() || "a LoRA",
        reason: lora.reason || "",
      }));
      const count = files.length;
      // Two causes and two fixes: a LoRA with no loader left wants one added;
      // one the shelf cannot identify has a loader and wants the file on the
      // shelf. Each LoRA's own line says which it is.
      const unnamed = loras.filter((lora) => !lora.sha256).length;
      const lead =
        unnamed === count
          ? `${count === 1 ? "A LoRA this recipe names is" : `${count} LoRAs this recipe names are`} missing from your model shelf, so ${count === 1 ? "it is" : "they are"} not applied. Put the ${count === 1 ? "file" : "files"} on the shelf, or take ${count === 1 ? "it" : "them"} off the recipe.`
          : unnamed === 0
            ? `${count === 1 ? "A LoRA this recipe names has" : `${count} LoRAs this recipe names have`} no loader to go in on this workflow, so ${count === 1 ? "it is" : "they are"} not applied. Add a loader to the workflow, or take ${count === 1 ? "it" : "them"} off the recipe.`
            : `${count} LoRAs this recipe names are not applied. Each one below says why.`;
      return read(
        lead,
        FIX_EDIT_LORAS,
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
    case "pixlstash_nodes": {
      // One sentence per refused ComfyUI-PixlStash node (#1521), named by its
      // title; an older server sends no `nodes` and gets the old sentence.
      const nodes = (reason.nodes || []).filter(Boolean);
      if (!nodes.length) {
        return read("This graph calls back into PixlStash, so PixlStash will not run it.");
      }
      return read(nodes.map(pixlstashNodeSentence).join(" "));
    }
    case "no_save_node":
      return read("This graph saves no image, so a run would produce nothing to keep.");
    case "a1111":
      return read("This picture was made in A1111 or Forge, so there is no ComfyUI graph to run.");
    case "ui_format":
      return read(
        "The workflow file this card names is an editor export, which ComfyUI cannot be handed.",
      );
    case PICTURE_INPUT_UNFILLED: {
      // Named by `title`, never by `slot_label`: the label is a topology hash.
      // The fix is choosing a picture, which the Run popup offers in its own
      // Pictures section rather than as a button here - so this sentence is
      // what a popup WITHOUT that section (Make more like these) says.
      const titles = names(reason.inputs, "title");
      const what = titles.length ? titles.join(", ") : "one of its picture inputs";
      return read(
        `This workflow needs a picture chosen for ${what}. Run it on its own to choose one.`,
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
 * @returns {Array<Object>} zero, one or two entries — the missing files and
 *   the owner's skips are two notices — so a caller can spread it.
 */
export function bypassNotice(group) {
  const all = (group?.bypassed_loras || []).filter(Boolean);
  // `requested` absent is the server's own bypass: that is what the list
  // meant before the owner could skip anything, and it must keep its warning.
  const missing = all.filter((model) => !model.requested);
  const skipped = all.filter((model) => model.requested);
  return [
    ...(missing.length ? [{ code: LORAS_BYPASSED, models: missing }] : []),
    ...(skipped.length ? [{ code: LORAS_SKIPPED, models: skipped }] : []),
  ];
}

/**
 * The recipe LoRAs of one pre-flight group that found no loader, in the
 * reason shape.
 *
 * Kept out of `reasons` for the reason `bypassNotice` is: the run goes ahead.
 * `workflowKey` rides along because the fix is "Edit LoRAs…" on THAT card,
 * and in a batch every group is a different one.
 *
 * @param {{workflow_key?: string, unplaced_loras?: Array<Object>}} group
 * @returns {Array<Object>} zero or one entry, so a caller can spread it.
 */
export function unplacedNotice(group) {
  const loras = (group?.unplaced_loras || []).filter(Boolean);
  return loras.length
    ? [{ code: LORAS_UNPLACED, loras, workflowKey: group?.workflow_key || "" }]
    : [];
}

/**
 * The replaced nodes of one pre-flight group, in the reason shape.
 *
 * Kept out of `reasons` for the reason `bypassNotice` is.
 *
 * @param {{replaced_nodes?: Array<Object>}} group
 * @returns {Array<Object>} zero or one entry, so a caller can spread it.
 */
export function replacedNotice(group) {
  const nodes = (group?.replaced_nodes || []).filter(Boolean);
  return nodes.length ? [{ code: NODES_REPLACED, nodes }] : [];
}

/**
 * Every repair the server made to one group's graph, as notices (#1463).
 *
 * The server's repair registry is one mechanism with one report per repair;
 * this is its one reading, so a popup spreads this and not each notice.
 *
 * @param {Object} group - one pre-flight group.
 * @returns {Array<Object>}
 */
export function repairNotices(group) {
  return [...bypassNotice(group), ...replacedNotice(group)];
}
