// triggerWords.js - a LoRA's trigger word, and where a prompt names it.
//
// The Run popup suggests the trigger word of the person's LoRA while the
// prompt lacks it, and `AppTextarea` marks it once it is typed.

/**
 * The trigger word a shelf row asks for, or "" when it needs none.
 *
 * The FIRST word, as the shelf's chip shows it (`triggerChip`): a file trained
 * on tagged captions records its whole tag table, most frequent first, and
 * the rest of that table is not something a prompt has to carry.
 *
 * @param {Object} row - a model row from the API.
 */
export function triggerWord(row) {
  const words = Array.isArray(row?.trigger_words) ? row.trigger_words : [];
  return String(words[0] ?? "").trim();
}

/**
 * *text* cut where it names one of *words*: `[{text, hit}]`, in order.
 *
 * Whole words only and any case, so "mira" is found in "Mira, smiling" and
 * not in "admiral".
 *
 * @param {string} text
 * @param {string[]} words
 */
export function markWords(text, words) {
  const source = String(text ?? "");
  const wanted = (words || []).map((word) => String(word).trim()).filter(Boolean);
  if (!wanted.length) return [{ text: source, hit: false }];
  const escaped = wanted
    // Longest first, so a phrase wins over a word it starts with.
    .sort((a, b) => b.length - a.length)
    .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}_])(?:${escaped.join("|")})(?![\\p{L}\\p{N}_])`,
    "giu",
  );
  const parts = [];
  let last = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > last) {
      parts.push({ text: source.slice(last, match.index), hit: false });
    }
    parts.push({ text: match[0], hit: true });
    last = match.index + match[0].length;
  }
  if (last < source.length || !parts.length) {
    parts.push({ text: source.slice(last), hit: false });
  }
  return parts;
}

/** Whether *text* names *word*, by the rule {@link markWords} marks with. */
export function namesWord(text, word) {
  return markWords(text, [word]).some((part) => part.hit);
}
