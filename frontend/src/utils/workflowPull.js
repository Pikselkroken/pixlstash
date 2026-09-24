// What a finished pull from ComfyUI found, as the lines the Workflows screen
// shows (#1440). Pure, so the wording is tested without mounting anything.
//
// **Four states that must not look alike** (the issue's rule, and
// `docs/design/visual-language.md` §4): a workflow that uses nodes this
// ComfyUI lacks will not run HERE (`error`, and "won't run on this ComfyUI",
// never "broken"); one naming a model file ComfyUI does not list is a warning,
// because the name is read by position and may be wrong; and anything that
// could not be checked is its own neutral kind, so "not checked" never passes
// for "fine". Each kind has its own glyph, so the difference survives without
// colour.

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** The host of a ComfyUI URL, which is how the verdicts say which machine. */
export function comfyuiHost(url) {
  if (!url) return "this ComfyUI";
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);

/**
 * `{headline, lines}` for a pull summary.
 *
 * Each line is `{kind: "error"|"warning"|"unchecked"|"info", icon, text,
 * names?, namesLabel?, action?}`. `names` is the list a line can be opened
 * to show; `action` names the one control a line offers
 * (`"show-one-offs"`).
 *
 * @param {Object} summary - `GET /comfyui/workflows/pull`'s `summary`.
 * @param {?string} url - the ComfyUI it came from.
 * @param {{hideOneOffs?: boolean}} [grid] - the grid's filters, so a line
 *   about hidden one-offs is only shown while they ARE hidden.
 */
export function pullSummaryLines(summary, url, { hideOneOffs = true } = {}) {
  const s = summary || {};
  const host = comfyuiHost(url);
  const counted = [];
  if (s.pulled) counted.push(`${s.pulled} new`);
  if (s.changed) counted.push(`${s.changed} changed`);
  if (s.matched) counted.push(`${s.matched} already here`);
  if (s.already_shipped)
    counted.push(`${s.already_shipped} shipped with PixlStash`);
  const listed = s.listed ?? 0;
  // "Found", not "Pulled": a pull whose every file failed pulled nothing.
  const headline = listed
    ? `Found ${plural(listed, "workflow", "workflows")} on ${host}` +
      (counted.length ? `: ${counted.join(", ")}.` : ".")
    : `${capital(host)} has no saved workflows to pull.`;

  const lines = [];
  if (s.known_from_pictures) {
    const n = s.known_from_pictures;
    lines.push({
      kind: "info",
      icon: "image-multiple-outline",
      text: `${n} of them ${n === 1 ? "was" : "were"} already known from your pictures.`,
    });
  }
  if (s.missing_nodes) {
    const n = s.missing_nodes;
    lines.push({
      kind: "error",
      icon: "puzzle-remove-outline",
      text: `${plural(n, "workflow", "workflows")} won't run on ${host}: ${n === 1 ? "it uses" : "they use"} nodes that ComfyUI doesn't have.`,
      names: s.missing_node_classes || [],
      namesLabel: "Nodes it doesn't have",
    });
  }
  if (s.missing_models) {
    lines.push({
      kind: "warning",
      icon: "file-alert-outline",
      text: `${plural(s.missing_models, "workflow names a model file", "workflows name model files")} ${host} doesn't list.`,
      names: s.missing_model_files || [],
      namesLabel: "Model files it doesn't list",
    });
  }
  // Every unchecked line SAYS "Not checked", so none of them can be read as
  // a plain fact about the pull.
  if (s.nodes_checked === false) {
    lines.push({
      kind: "unchecked",
      icon: "help-circle-outline",
      text: `Not checked: PixlStash couldn't read which nodes and models ${host} has.`,
    });
  } else {
    if (s.nodes_unchecked) {
      lines.push({
        kind: "unchecked",
        icon: "help-circle-outline",
        text: `Not checked on ${host}: ${plural(s.nodes_unchecked, "workflow", "workflows")} couldn't be read.`,
      });
    }
    if (s.models_unread) {
      lines.push({
        kind: "unchecked",
        icon: "help-circle-outline",
        text: `Not checked on ${host}: ${plural(s.models_unread, "model file", "model files")} in loaders PixlStash can't read.`,
      });
    }
  }
  const written = (s.pulled || 0) + (s.changed || 0);
  if (written && hideOneOffs) {
    // Where they went: a pulled workflow with no pictures, rating or saved
    // look is a one-off (`Card.hand_imported`), and the grid hides those while
    // the filter is on. "Can", because a pulled file may land on a card that
    // has pictures and is not a one-off at all.
    lines.push({
      kind: "info",
      icon: "eye-off-outline",
      text: "A pulled workflow with no pictures yet counts as a one-off, which the grid leaves out.",
      action: "show-one-offs",
    });
  }
  if (s.changed) {
    lines.push({
      kind: "info",
      icon: "file-replace-outline",
      text: `${plural(s.changed, "workflow was", "workflows were")} edited in ${host} since the last pull. The new ${s.changed === 1 ? "version is" : "versions are"} stored beside the earlier ${s.changed === 1 ? "copy" : "copies"}, which ${s.changed === 1 ? "stays" : "stay"}.`,
    });
  }
  if (s.skipped_dismissed) {
    lines.push({
      kind: "info",
      icon: "minus-circle-outline",
      text: `${plural(s.skipped_dismissed, "workflow", "workflows")} you deleted here ${s.skipped_dismissed === 1 ? "was" : "were"} left out.`,
    });
  }
  if (written || s.skipped_dismissed) {
    lines.push({
      kind: "info",
      icon: "information-outline",
      text: "A workflow you delete in PixlStash stays out of later pulls. One removed by hand from PixlStash's workflows folder comes back.",
    });
  }
  if (s.failed) {
    lines.push({
      kind: "error",
      icon: "close-circle-outline",
      text: `${plural(s.failed, "workflow", "workflows")} couldn't be read from ${host} or stored here.`,
    });
  }
  if (s.gone) {
    lines.push({
      kind: "info",
      icon: "information-outline",
      text: `${plural(s.gone, "workflow", "workflows")} pulled before ${s.gone === 1 ? "is" : "are"} no longer on ${host}. The ${s.gone === 1 ? "copy" : "copies"} here ${s.gone === 1 ? "stays" : "stay"}.`,
    });
  }
  return { headline, lines };
}
