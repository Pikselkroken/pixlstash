/**
 * The layout string, read and written by LibraryLayoutDialog (v1.11 4b/4c).
 *
 * `project/person,set` is the whole grammar: `/` separates segments, one folder
 * level each in order; `,` separates a segment's alternatives, and the first the
 * picture has a value for wins; a segment nothing fills is *skipped* rather than
 * left as an empty folder, which is what keeps the tree two deep instead of
 * five.
 *
 * This module is the grammar and nothing else. **What a picture's folder
 * actually becomes is the server's answer** - `pixlstash/utils/library_layout.py`
 * does the sanitising (`folder_name`, NFC case folding, the unfiled rules) and
 * the migration preview is what counts real files. Nothing here may grow into a
 * second renderer.
 */

/** The facets a segment may hold, in the order the builder offers them. */
export const LAYOUT_FACETS = [
  { value: "project", label: "Project" },
  { value: "person", label: "Person" },
  { value: "set", label: "Set" },
  { value: "tag", label: "Tag" },
];

const FACET_VALUES = new Set(LAYOUT_FACETS.map((facet) => facet.value));
const LABELS = Object.fromEntries(
  LAYOUT_FACETS.map((facet) => [facet.value, facet.label]),
);

/**
 * Turn `"project/person,set"` into `[["project"], ["person", "set"]]`.
 *
 * An unknown facet is dropped rather than shown: the builder can only offer the
 * four it knows, so keeping one would render a level the owner cannot edit and
 * would silently delete on the next save.
 *
 * @param {string|null|undefined} text
 * @returns {string[][]}
 */
export function parseLayout(text) {
  if (!text) return [];
  return text
    .split("/")
    .map((segment) =>
      segment
        .split(",")
        .map((facet) => facet.trim().toLowerCase())
        .filter((facet) => FACET_VALUES.has(facet)),
    )
    .filter((segment) => segment.length > 0);
}

/**
 * Turn `[["project"], ["person", "set"]]` back into `"project/person,set"`.
 *
 * @param {string[][]} segments
 * @returns {string|null} `null` for an empty layout, which is how the API spells
 *   "no layout" - deliberately not `""`, so a caller cannot send a value that
 *   the PATCH would have to guess about.
 */
export function formatLayout(segments) {
  const text = (segments || [])
    .map((segment) =>
      (segment || []).filter((facet) => FACET_VALUES.has(facet)).join(","),
    )
    .filter(Boolean)
    .join("/");
  return text || null;
}

/** `["person", "set"]` -> `"Person or Set"`, the artboard's own wording. */
export function describeSegment(segment) {
  const labels = (segment || []).map((facet) => LABELS[facet] || facet);
  if (labels.length === 0) return "";
  if (labels.length === 1) return labels[0];
  return `${labels.slice(0, -1).join(", ")} or ${labels[labels.length - 1]}`;
}
