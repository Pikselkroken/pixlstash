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

/**
 * `{headline, lines}` for a pull summary.
 *
 * Each line is `{kind: "error"|"warning"|"unchecked"|"info", icon, text,
 * names?}`, where `names` is the list a line can be opened to show.
 *
 * @param {Object} summary - `GET /comfyui/workflows/pull`'s `summary`.
 * @param {?string} url - the ComfyUI it came from.
 */
export function pullSummaryLines(summary, url) {
  const s = summary || {};
  const host = comfyuiHost(url);
  const counted = [];
  if (s.pulled) counted.push(`${s.pulled} new`);
  if (s.matched) counted.push(`${s.matched} already here`);
  if (s.already_shipped)
    counted.push(`${s.already_shipped} shipped with PixlStash`);
  const listed = s.listed ?? 0;
  const headline = listed
    ? `Pulled ${plural(listed, "workflow", "workflows")} from ${host}` +
      (counted.length ? `: ${counted.join(", ")}.` : ".")
    : `${host} has no saved workflows to pull.`;

  const lines = [];
  if (s.known_from_pictures) {
    lines.push({
      kind: "info",
      icon: "image-multiple-outline",
      text: `${plural(s.known_from_pictures, "is a workflow", "are workflows")} your pictures were already made with.`,
    });
  }
  if (s.missing_nodes) {
    lines.push({
      kind: "error",
      icon: "alert-circle-outline",
      text: `${s.missing_nodes} won't run on ${host}: ${s.missing_nodes === 1 ? "it uses" : "they use"} nodes it doesn't have.`,
      names: s.missing_node_classes || [],
      namesLabel: "Nodes it doesn't have",
    });
  }
  if (s.missing_models) {
    lines.push({
      kind: "warning",
      icon: "file-alert-outline",
      text: `${plural(s.missing_models, "names", "name")} a model file ${host} doesn't list.`,
      names: s.missing_model_files || [],
      namesLabel: "Model files it doesn't list",
    });
  }
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
        text: `${plural(s.nodes_unchecked, "workflow", "workflows")} couldn't be read, so ${s.nodes_unchecked === 1 ? "it wasn't" : "they weren't"} checked.`,
      });
    }
    if (s.models_unread) {
      lines.push({
        kind: "unchecked",
        icon: "help-circle-outline",
        text: `${plural(s.models_unread, "model file wasn't", "model files weren't")} checked: PixlStash couldn't tell which loader ${s.models_unread === 1 ? "it belongs" : "they belong"} to.`,
      });
    }
  }
  if (s.pulled) {
    // Where they went: a pulled workflow with no pictures is a one-off
    // (`Card.hand_imported`), and the grid hides those by default. Without
    // this the reader sees "21 new" over a grid that looks unchanged.
    lines.push({
      kind: "info",
      icon: "eye-off-outline",
      text: `New workflows with no pictures yet count as one-offs, which the grid leaves out while Filters › Hide one-offs is on.`,
    });
  }
  if (s.skipped_dismissed) {
    lines.push({
      kind: "info",
      icon: "delete-outline",
      text: `${plural(s.skipped_dismissed, "workflow", "workflows")} you deleted here ${s.skipped_dismissed === 1 ? "was" : "were"} left out. A file removed from the folder by hand comes back on the next pull.`,
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
      text: `${plural(s.gone, "workflow", "workflows")} pulled before ${s.gone === 1 ? "is" : "are"} no longer in ${host}. The ${s.gone === 1 ? "copy" : "copies"} here ${s.gone === 1 ? "stays" : "stay"}.`,
    });
  }
  return { headline, lines };
}
