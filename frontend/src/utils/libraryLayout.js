/**
 * The layout string, read and written by the Library layout pane (v1.11 4b/4c).
 *
 * `project/person,set` is the whole grammar: `/` separates segments, one folder
 * level each in order; `,` separates a segment's alternatives, and the first the
 * picture has a value for wins; a segment nothing fills is *skipped* rather than
 * left as an empty folder, which is what keeps the tree two deep instead of
 * five.
 *
 * `layoutExamples` shows what a layout would do to four example pictures as the
 * builder changes, because a segment being *skipped* is the least obvious thing
 * about the grammar and one worked example teaches it faster than the sentence
 * under it does. A round trip per edit to learn that would be a round trip to
 * learn something the client already knows.
 *
 * **The renderer behind it is deliberately private and deliberately partial.**
 * It reproduces only the two rules the examples demonstrate - first match wins,
 * an unfilled segment is skipped - and none of the sanitising
 * `pixlstash/utils/library_layout.py::render` does (`folder_name`, NFC case
 * folding, the unfiled-is-not-a-level rules). That is safe because its only
 * inputs are the four constant fixtures below, and exporting it would invite a
 * caller with real names for whom it would be quietly wrong. What a picture's
 * folder actually becomes is the server's answer, and the migration preview is
 * the one that counts real files.
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

/**
 * Where one of the fixtures below would go. **Not a general renderer** - see the
 * module docstring for why it is private and what it deliberately omits.
 *
 * @param {Object} facets - `{project, person, set, tag}`, each a name or unset.
 * @param {string[][]} segments
 * @param {string} unfiled - the unfiled folder's name.
 * @returns {string[]} the folder components, never empty: a picture that fills
 *   no segment at all gets `[unfiled]`.
 */
function renderExample(facets, segments, unfiled) {
  const parts = [];
  for (const segment of segments || []) {
    // First match wins inside a segment, and a segment nothing fills is
    // skipped - not left as an empty folder.
    const facet = (segment || []).find((name) => facets?.[name]);
    if (facet) parts.push(facets[facet]);
  }
  return parts.length ? parts : [unfiled];
}

/** The sample picture the examples are rendered against. */
const SAMPLE = {
  project: "2024 Shoots",
  person: "Mira",
  set: "mira-lora-v3",
  tag: "portrait",
};

/**
 * The artboard's example strip: what this layout does to four kinds of picture.
 *
 * Shown live under the builder because a segment being *skipped* is the least
 * obvious thing about the grammar, and one worked example teaches it faster than
 * the sentence underneath does.
 *
 * @param {string[][]} segments
 * @param {string} unfiled
 * @returns {Array<{caption: string, folder: string}>}
 */
export function layoutExamples(segments, unfiled) {
  const cases = [
    { caption: "has a project and a person", has: ["project", "person"] },
    { caption: "has a project and a set", has: ["project", "set"] },
    { caption: "has only a set", has: ["set"] },
    { caption: "has nothing to file it by", has: [] },
  ];
  return cases.map(({ caption, has }) => {
    const facets = Object.fromEntries(has.map((name) => [name, SAMPLE[name]]));
    return {
      caption,
      folder: `${renderExample(facets, segments, unfiled).join(" / ")} /`,
    };
  });
}
