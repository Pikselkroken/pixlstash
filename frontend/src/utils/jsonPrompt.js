/**
 * Prompts written as JSON (a caption model's structured output fed straight
 * to a text encoder, or a hand-written "JSON prompt") read as one wall of
 * braces and quotes when printed as text. These turn one into something a
 * person can read, without changing the prompt itself: copying and re-running
 * still use the raw text.
 */

const isScalar = (value) => value === null || typeof value !== "object";

// Past these the outline stops being something to read and the raw text is
// shown instead. A captioner's JSON is a few thousand characters, a handful of
// levels and a few dozen fields; the caps keep a pathological prompt (a file
// from anywhere can carry one) from overflowing the stack or mounting a row
// per field by the hundred thousand.
const MAX_TEXT = 50000;
const MAX_DEPTH = 12;
const MAX_ROWS = 400;
const TOO_BIG = Symbol("too big");

function humanKey(key) {
  const words = String(key)
    .replace(/([a-z])([A-Z])/g, (_, a, b) => `${a} ${b.toLowerCase()}`)
    .replace(/[_-]+/g, " ")
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// `depth` is the indent, which a list entry does not add to; `nesting` counts
// every level, so nested lists hit the cap too.
function walk(value, key, depth, rows, nesting = 0) {
  if (nesting > MAX_DEPTH || rows.length > MAX_ROWS) throw TOO_BIG;
  if (value === null || value === "") return;
  if (isScalar(value)) {
    rows.push({ depth, key, text: String(value) });
    return;
  }
  // A list of plain values is one line: tags as "a, b, c", a bounding box as
  // its four numbers.
  if (Array.isArray(value) && value.every(isScalar)) {
    const kept = value.filter((item) => item !== null && item !== "");
    if (kept.length) {
      rows.push({
        depth,
        key,
        text: kept.join(", "),
        numeric: kept.every((item) => typeof item === "number"),
      });
    }
    return;
  }
  const start = rows.length;
  if (key) rows.push({ depth, key, text: null });
  const inner = key ? depth + 1 : depth;
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      const first = rows.length;
      walk(item, "", inner, rows, nesting + 1);
      // Entries of a list sit at one depth, so a rule is what tells one from
      // the next.
      if (index > 0 && rows[first]) rows[first].divider = true;
    });
  } else {
    for (const [childKey, child] of Object.entries(value)) {
      walk(child, humanKey(childKey), inner, rows, nesting + 1);
    }
  }
  // A heading over nothing (every child was empty) says nothing.
  if (key && rows.length === start + 1) rows.pop();
}

/**
 * The prompt as an outline when it is a JSON object or array with named
 * fields, else null: plain text, something that merely starts with a brace
 * (a `{red|blue}` wildcard), a bare list, or JSON too large or too deep to
 * read as an outline.
 *
 * Each row is `{depth, key, text, numeric?, divider?}`: `key` is the field
 * name made readable ("" for a list entry), `text` is null on a row that only
 * heads the rows under it, `numeric` marks a list of numbers (coordinates,
 * not prose), and `divider` starts the second and later entries of a list.
 *
 * @param {string|null|undefined} text
 * @returns {Array<Object>|null}
 */
export function promptOutline(text) {
  if (typeof text !== "string" || text.length > MAX_TEXT) return null;
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  let value;
  try {
    value = JSON.parse(trimmed);
  } catch {
    // Not an error: the prompt is text that happens to open with a brace.
    return null;
  }
  const rows = [];
  try {
    walk(value, "", 0, rows);
  } catch (error) {
    if (error !== TOO_BIG) throw error;
    return null;
  }
  // No field names anywhere means a bare list like `[1]` or `["a", "b"]`,
  // which reads better as typed than as an outline of nothing.
  return rows.some((row) => row.key) ? rows : null;
}

/**
 * The prompt's prose for a line-clamped preview: a JSON prompt's text values
 * in order, without its keys, braces or coordinates. Plain text comes back as
 * it is.
 *
 * @param {string|null|undefined} text
 * @returns {string}
 */
export function promptPreview(text) {
  const rows = promptOutline(text);
  const prose = (rows || [])
    .filter((row) => row.text && !row.numeric)
    .map((row) => row.text)
    .join(" ");
  // Plain text, or JSON with nothing but numbers in it: show it as written.
  return prose || text || "";
}
