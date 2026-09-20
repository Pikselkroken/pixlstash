/**
 * The grid's active filters as chips, and the choices the filter menu offers.
 *
 * One module for both so the menu row and the strip chip it produces can never
 * spell a choice two ways. Every chip carries its own `remove`, which is the
 * only way the strip changes a filter: editing a chip in place is out of scope.
 */

// Pick-one Picture kinds. "Any" is deliberately not a choice: removing the chip
// is how you get back to any.
export const MEDIA_OPTIONS = [
  { id: "images", label: "Images", chip: "images", icon: "mdi-image-outline" },
  { id: "videos", label: "Video", chip: "video", icon: "mdi-video-outline" },
];

export const FACE_OPTIONS = [
  {
    id: "with_face",
    label: "Has face",
    chip: "has face",
    icon: "mdi-face-recognition",
  },
  {
    id: "without_face",
    label: "No face",
    chip: "no face",
    icon: "mdi-account-off-outline",
  },
];

// 'unresolved' is honoured by the store and API but not offered: the duplicate
// queue owns undecided groups.
export const STACK_OPTIONS = [
  {
    id: "stacked",
    label: "Stacked",
    chip: "stacked",
    icon: "mdi-layers-outline",
  },
  {
    id: "unstacked",
    label: "Unstacked",
    chip: "unstacked",
    icon: "mdi-layers-off",
  },
];

// Only the combinations the backend actually detects. Each maps to one
// `impossible_tag_source` value.
export const IMPOSSIBLE_OPTIONS = [
  { id: "no_face", label: "Face tags, no face", chip: "face tags, no face" },
  {
    id: "no_humans",
    label: "People tags, no humans",
    chip: "people tags, no humans",
  },
];

export const CONFIDENCE_THRESHOLDS = [0.9, 0.8, 0.7, 0.6];

export const SMART_SCORE_BUCKET_LABELS = {
  unscored: "unscored",
  "1-2": "1–2",
  "2-3": "2–3",
  "3-4": "3–4",
  "4-5": "4–5",
};

export const RESOLUTION_BUCKET_LABELS = {
  unknown: "unknown",
  lt1mp: "under 1 MP",
  "1-4mp": "1–4 MP",
  "4-8mp": "4–8 MP",
  "8-16mp": "8–16 MP",
  "16plus": "16 MP+",
};

/** `"hat:0.80"` → `{ tag: "hat", threshold: 0.8 }`. Tags may hold a colon. */
export function parseConfidenceEntry(entry) {
  const at = String(entry).lastIndexOf(":");
  return {
    tag: entry.slice(0, at),
    threshold: parseFloat(entry.slice(at + 1)),
  };
}

export function confidenceEntry(tag, threshold) {
  return `${tag}:${threshold.toFixed(2)}`;
}

export function percent(threshold) {
  return `${Math.round(threshold * 100)}%`;
}

/** A model or LoRA file name without its extension, as the menus list it. */
export function modelLabel(name) {
  return String(name).replace(/\.[^/.]+$/, "");
}

/** "3–4", "3+", "up to 4", with the unscored part when it is on. */
export function scoreChipValue(min, max, unscored) {
  let range = "";
  if (min != null && max != null)
    range = min === max ? `${min}` : `${min}–${max}`;
  else if (min != null) range = `${min}+`;
  else if (max != null) range = `up to ${max}`;
  if (!unscored) return range;
  return range ? `${range} or unscored` : "unscored";
}

function without(list, value) {
  return (list || []).filter((v) => v !== value);
}

/**
 * The chips for the store's current filters, in strip order.
 *
 * @param {object} store the filter store
 * @param {{ allPicturesView?: boolean }} [view] "No character" only exists in
 *   All Pictures, so a stale flag elsewhere is not shown as a filter.
 * @returns {Array<{ key: string, kind: string, value: string, remove: Function }>}
 */
export function filterChips(store, { allPicturesView = true } = {}) {
  const chips = [];
  const push = (key, kind, value, remove) =>
    chips.push({ key, kind, value, remove });

  if (allPicturesView && store.unassignedOnlyFilter) {
    push("problem:no_character", "Problem", "no character", () => {
      store.unassignedOnlyFilter = false;
    });
  }
  for (const source of store.impossibleSources || []) {
    const opt = IMPOSSIBLE_OPTIONS.find((o) => o.id === source);
    push(`problem:${source}`, "Problem", opt ? opt.chip : source, () => {
      store.impossibleSources = without(store.impossibleSources, source);
    });
  }

  const media = MEDIA_OPTIONS.find((o) => o.id === store.mediaTypeFilter);
  if (media) {
    push("media", "Media", media.chip, () => {
      store.mediaTypeFilter = "all";
    });
  }
  const faces = FACE_OPTIONS.find((o) => o.id === store.faceBboxFilter);
  if (faces) {
    push("faces", "Faces", faces.chip, () => {
      store.faceBboxFilter = null;
    });
  }
  // "unresolved" is not offered in the menu but can arrive from a URL; it still
  // gets a chip, or the grid would be narrowed with nothing saying so.
  const stacks = [
    ...STACK_OPTIONS,
    { id: "unresolved", chip: "unresolved" },
  ].find((o) => o.id === store.stackStateFilter);
  if (stacks) {
    push("stacks", "Stacks", stacks.chip, () => {
      store.stackStateFilter = "all";
    });
  }
  if (store.sharedOnlyFilter) {
    push("sharing", "Sharing", "shared", () => {
      store.sharedOnlyFilter = false;
    });
  }

  const score = scoreChipValue(
    store.minScoreFilter,
    store.maxScoreFilter,
    store.unscoredOnlyFilter,
  );
  if (score) {
    push("score", "Score", score, () => {
      store.minScoreFilter = null;
      store.maxScoreFilter = null;
      store.unscoredOnlyFilter = false;
    });
  }
  // Smart score and Resolution have no row in the filter menu: they are set
  // from the stats sidebar's charts. They still get chips, so the strip names
  // every filter narrowing the grid and Clear all reaches them.
  if (store.smartScoreBucketFilter != null) {
    const b = store.smartScoreBucketFilter;
    push(
      "smart_score",
      "Smart score",
      SMART_SCORE_BUCKET_LABELS[b] ?? b,
      () => {
        store.smartScoreBucketFilter = null;
      },
    );
  }
  if (store.resolutionBucketFilter != null) {
    const b = store.resolutionBucketFilter;
    push("resolution", "Resolution", RESOLUTION_BUCKET_LABELS[b] ?? b, () => {
      store.resolutionBucketFilter = null;
    });
  }

  for (const tag of store.tagFilter || []) {
    push(`tag:${tag}`, "Has tag", tag, () => {
      store.tagFilter = without(store.tagFilter, tag);
    });
  }
  for (const tag of store.tagRejectedFilter || []) {
    push(`lacks:${tag}`, "Lacks tag", tag, () => {
      store.tagRejectedFilter = without(store.tagRejectedFilter, tag);
    });
  }
  for (const entry of store.tagConfidenceAboveFilter || []) {
    const { tag, threshold } = parseConfidenceEntry(entry);
    push(
      `missing:${entry}`,
      "Missing tag",
      `${tag} ${percent(threshold)}+`,
      () => {
        store.tagConfidenceAboveFilter = without(
          store.tagConfidenceAboveFilter,
          entry,
        );
      },
    );
  }
  for (const entry of store.tagConfidenceBelowFilter || []) {
    const { tag, threshold } = parseConfidenceEntry(entry);
    push(
      `doubtful:${entry}`,
      "Doubtful tag",
      `${tag} under ${percent(threshold)}`,
      () => {
        store.tagConfidenceBelowFilter = without(
          store.tagConfidenceBelowFilter,
          entry,
        );
      },
    );
  }

  for (const name of store.comfyuiModelFilter || []) {
    push(`model:${name}`, "Checkpoint", modelLabel(name), () => {
      store.comfyuiModelFilter = without(store.comfyuiModelFilter, name);
    });
  }
  for (const name of store.comfyuiLoraFilter || []) {
    push(`lora:${name}`, "LoRA", modelLabel(name), () => {
      store.comfyuiLoraFilter = without(store.comfyuiLoraFilter, name);
    });
  }
  // *Show all N pictures* on a workflow card (F7). No menu row offers it: the
  // Workflows screen is the only place that knows a card's key, so the chip is
  // how it arrives and its × is the whole of the way back.
  if (store.workflowFilter) {
    push("workflow", "Workflow", store.workflowFilter.name, () => {
      store.workflowFilter = null;
    });
  }
  return chips;
}
