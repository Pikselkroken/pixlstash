// The Edit tab's Paint tools: a selection on the open picture, kept as the
// steps the owner took rather than as a bitmap.
//
// The steps ARE the selection (`{version, steps, refine}`, the JSON
// `pixlstash/services/edit_selection.py` reads), so an edit can be saved as a
// small document and repeated. Geometry is in fractions of the picture, a brush
// radius a fraction of its shorter side, refine values in the picture's pixels.
//
// What is drawn is never rendered here: the server renders the mask
// (`POST /pictures/{id}/mask`) with the same code a run uses, so the wash and
// the marching ants show exactly the MASK the workflow gets. The gesture in
// progress is drawn as a plain shape until it is committed.

import { computed, onScopeDispose, reactive, ref, watch } from "vue";
import { renderPictureMask } from "../api/pictures";

/** The toolbox, in its order, with each tool's key. */
export const PAINT_TOOLS = [
  { id: "wand", label: "Colour wand", key: "W", icon: "mdi-auto-fix" },
  { id: "rect", label: "Rectangle", key: "R", icon: "mdi-selection" },
  { id: "ellipse", label: "Ellipse", key: "O", icon: "mdi-selection-ellipse" },
  { id: "lasso", label: "Lasso", key: "L", icon: "mdi-lasso" },
  { id: "brush", label: "Brush", key: "B", icon: "mdi-brush" },
  { id: "eraser", label: "Eraser", key: "E", icon: "mdi-eraser" },
  { id: "crop", label: "Crop", key: "C", icon: "mdi-crop" },
  { id: "hand", label: "Hand", key: "H", icon: "mdi-hand-back-right-outline" },
];

/** New / Add / Subtract / Intersect, the selection tools' combine modes. */
export const PAINT_MODES = [
  { id: "new", label: "New", icon: "mdi-checkbox-blank-outline" },
  { id: "add", label: "Add", icon: "mdi-plus-box-multiple-outline" },
  { id: "subtract", label: "Subtract", icon: "mdi-minus-box-multiple-outline" },
  { id: "intersect", label: "Intersect", icon: "mdi-vector-intersection" },
];

/** The tools the combine mode applies to. A brush adds, an eraser takes away. */
export const MODE_TOOLS = new Set(["wand", "rect", "ellipse", "lasso"]);

const PREVIEW_DELAY_MS = 120;
// A path point closer than this to the last one (a fraction of the picture)
// adds nothing a mask could show, and a long stroke stays a short document.
const MIN_POINT_STEP = 0.002;
// Tolerance gained by dragging the wand across the whole picture.
const WAND_DRAG_RANGE = 255;

function round(value) {
  return Math.round(value * 10000) / 10000;
}

function point(p) {
  return [round(p.x), round(p.y)];
}

/**
 * @param {Object} deps
 * @param {import("vue").Ref<number|string|null>} deps.pictureId - the open picture.
 * @param {import("vue").Ref<{naturalWidth: number, naturalHeight: number}>} deps.dims
 *   - the picture's pixel size, for the brush radius.
 */
export function usePaintSelection({ pictureId, dims }) {
  const tool = ref("hand");
  const mode = ref("new");
  // The brush's radius in the picture's pixels; sent as a fraction.
  const brushSize = ref(24);
  const tolerance = ref(32);
  const steps = ref([]);
  // Steps taken back, latest last, for redo; a new step forgets them.
  const undone = ref([]);
  const refine = reactive({ grow: 0, feather: 0, fill_holes: 0 });
  // The gesture in progress, in fractions: `{tool, start, end}` or `{tool, points}`.
  const draft = ref(null);
  // The crop frame the owner is setting, `[left, top, width, height]`.
  const cropBox = ref(null);
  // The eye: the mask shown in black and white, as MASK receives it.
  const showMask = ref(false);
  // The server's rendering of the selection, as an object URL.
  const maskUrl = ref("");
  const maskError = ref("");

  const hasSelection = computed(() => steps.value.length > 0);
  const canRedo = computed(() => undone.value.length > 0);
  const selection = computed(() => ({
    version: 1,
    steps: steps.value,
    refine: { ...refine },
  }));

  function shorterSide() {
    const { naturalWidth = 1, naturalHeight = 1 } = dims.value || {};
    return Math.max(1, Math.min(naturalWidth, naturalHeight));
  }

  function stepMode(modifiers = {}) {
    if (modifiers.shift) return "add";
    if (modifiers.alt) return "subtract";
    return mode.value;
  }

  // A new gesture is drawn to be seen on the picture, so it leaves the
  // black-and-white view; the eye is a look, not a mode.
  function commit(step) {
    steps.value = [...steps.value, step];
    undone.value = [];
    showMask.value = false;
  }

  /** A pointer went down at `p` (fractions) with the tool in hand. */
  function begin(p, modifiers = {}) {
    const chosen = tool.value;
    if (chosen === "hand") return;
    if (chosen === "wand") {
      commit({
        tool: "wand",
        mode: stepMode(modifiers),
        point: point(p),
        tolerance: tolerance.value,
      });
      // As in GIMP: dragging on from the click retunes that region's
      // tolerance, right or down for more, left or up for less.
      draft.value = { tool: "wand", start: p, tolerance: tolerance.value };
      return;
    }
    if (chosen === "rect" || chosen === "ellipse" || chosen === "crop") {
      draft.value = { tool: chosen, start: p, end: p, mode: stepMode(modifiers) };
    } else {
      draft.value = { tool: chosen, points: [p], mode: stepMode(modifiers) };
    }
  }

  function move(p) {
    const current = draft.value;
    if (!current) return;
    if (current.tool === "wand") {
      const drag = p.x - current.start.x + (p.y - current.start.y);
      const retuned = Math.max(
        0,
        Math.min(255, Math.round(current.tolerance + drag * WAND_DRAG_RANGE)),
      );
      const last = steps.value[steps.value.length - 1];
      if (last?.tool !== "wand" || last.tolerance === retuned) return;
      tolerance.value = retuned;
      steps.value = [...steps.value.slice(0, -1), { ...last, tolerance: retuned }];
      return;
    }
    if (current.points) {
      const last = current.points[current.points.length - 1];
      if (Math.hypot(p.x - last.x, p.y - last.y) < MIN_POINT_STEP) return;
      draft.value = { ...current, points: [...current.points, p] };
    } else {
      draft.value = { ...current, end: p };
    }
  }

  function end() {
    const current = draft.value;
    draft.value = null;
    if (!current || current.tool === "wand") return;
    if (current.start) {
      const box = [
        round(Math.min(current.start.x, current.end.x)),
        round(Math.min(current.start.y, current.end.y)),
        round(Math.abs(current.end.x - current.start.x)),
        round(Math.abs(current.end.y - current.start.y)),
      ];
      // A click without a drag draws nothing.
      if (box[2] < 0.005 || box[3] < 0.005) return;
      if (current.tool === "crop") {
        cropBox.value = box;
        return;
      }
      commit({ tool: current.tool, mode: current.mode, box });
      return;
    }
    if (current.tool === "lasso") {
      if (current.points.length < 3) return;
      commit({ tool: "lasso", mode: current.mode, points: current.points.map(point) });
      return;
    }
    commit({
      tool: "brush",
      mode: current.tool === "eraser" ? "subtract" : "add",
      // Never past the server's ceiling: the whole shorter side.
      radius: Math.min(1, round(brushSize.value / shorterSide()) || 0.0001),
      points: current.points.map(point),
    });
  }

  function cancel() {
    draft.value = null;
  }

  // Setters, so the components handed this state change it through it.
  function setTool(id) {
    tool.value = id;
  }

  function setMode(id) {
    mode.value = id;
  }

  function setBrushSize(px) {
    brushSize.value = Number(px);
  }

  function setTolerance(value) {
    tolerance.value = Number(value);
  }

  function setRefine(key, value) {
    refine[key] = Number(value);
  }

  function setCropBox(box) {
    cropBox.value = box;
  }

  function toggleMask() {
    showMask.value = !showMask.value;
  }

  function invert() {
    commit({ tool: "invert" });
  }

  function undo() {
    if (!steps.value.length) return;
    undone.value = [...undone.value, steps.value[steps.value.length - 1]];
    steps.value = steps.value.slice(0, -1);
  }

  function redo() {
    if (!undone.value.length) return;
    steps.value = [...steps.value, undone.value[undone.value.length - 1]];
    undone.value = undone.value.slice(0, -1);
  }

  function clear() {
    steps.value = [];
    undone.value = [];
    draft.value = null;
    showMask.value = false;
  }

  // A hand selection belongs to the picture it was drawn on.
  watch(pictureId, () => {
    clear();
    cropBox.value = null;
  });

  // ── the preview: the server's mask, a moment after the last change ──────
  let previewTimer = null;
  let previewAbort = null;

  function dropMask() {
    if (maskUrl.value) URL.revokeObjectURL(maskUrl.value);
    maskUrl.value = "";
  }

  async function fetchMask(id, body) {
    previewAbort?.abort();
    const abort = new AbortController();
    previewAbort = abort;
    try {
      const blob = await renderPictureMask(id, body, { signal: abort.signal });
      if (abort.signal.aborted) return;
      dropMask();
      maskUrl.value = URL.createObjectURL(blob);
      maskError.value = "";
    } catch (err) {
      if (abort.signal.aborted) return;
      console.warn(`Could not render the selection on picture ${id}:`, err);
      // The last mask is of another selection: not shown beside the error.
      dropMask();
      maskError.value = "Could not show the selection.";
    }
  }

  watch(
    selection,
    (body) => {
      clearTimeout(previewTimer);
      const id = Number(pictureId.value);
      if (!body.steps.length || !Number.isFinite(id) || id <= 0) {
        previewAbort?.abort();
        dropMask();
        maskError.value = "";
        return;
      }
      previewTimer = setTimeout(() => fetchMask(id, body), PREVIEW_DELAY_MS);
    },
    { deep: true },
  );

  onScopeDispose(() => {
    clearTimeout(previewTimer);
    previewAbort?.abort();
    dropMask();
  });

  return {
    tool,
    mode,
    brushSize,
    tolerance,
    steps,
    refine,
    draft,
    cropBox,
    showMask,
    maskUrl,
    maskError,
    hasSelection,
    canRedo,
    selection,
    begin,
    move,
    end,
    cancel,
    setTool,
    setMode,
    setBrushSize,
    setTolerance,
    setRefine,
    setCropBox,
    toggleMask,
    invert,
    undo,
    redo,
    clear,
  };
}
