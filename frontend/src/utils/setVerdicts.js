// The owner's verdicts on a workflow set and its members, and PixlStash's check
// beside them (docs/ideas/workflow-set-verdicts.md). The check is PixlStash's,
// the verdict is the owner's; nothing here merges the two.
//
// Every threshold (3 checked, 5 and 5 pictures, 40 points) lives on the server,
// which serves the finished `check` and `suspects`; this file only words them.

/** The server's join key: the member model ids, ascending, comma-joined. */
export function comboKey(ids) {
  return [...new Set(ids)].sort((a, b) => a - b).join(",");
}

/** `combo_key` -> entry, for `set_checks`. */
export function checksByKey(setChecks) {
  return new Map((setChecks ?? []).map((entry) => [entry.combo_key, entry]));
}

/** The shapes a verdict takes: a check for approval, a cross for the reverse. */
const POSITIVE = new Set(["yes", "not_problem"]);

export function isPositive(verdict) {
  return POSITIVE.has(verdict);
}

function pictures(n) {
  return n === 1 ? "1 picture" : `${n} pictures`;
}

/**
 * The note row for one check state, as the spec words it. `null` when the
 * state says nothing beyond the plain no-evidence sentence.
 *
 * @returns {{count: string, ask: string, caveat: string}|null}
 */
export function checkCopy(check) {
  if (!check) return null;
  const { together, checked, passing } = check.evidence ?? {};
  switch (check.check) {
    case "pending":
      return {
        count: `These have all run together in ${pictures(together)}. PixlStash is still checking them (${checked} of ${together} done).`,
      };
    case "too_few":
    case "pass":
    case "fail": {
      const count = `These have all run together, in ${pictures(together)}, ${passing} of which look like their prompt (PixlStash's automatic check).`;
      if (check.check === "pass") {
        return {
          count,
          ask: "PixlStash thinks this set produces sensible output. Do you agree?",
        };
      }
      if (check.check === "fail") {
        return {
          count,
          ask: "PixlStash thinks this set does not produce sensible output. Do you agree?",
          caveat:
            "This catches noise and unrelated output, not style or quality.",
        };
      }
      return { count };
    }
    case "unavailable":
      return { unchecked: "PixlStash has not checked these pictures." };
    default:
      return null;
  }
}

/** The owner's word on one member, suspect today or not; null if none. */
export function memberVerdictOf(check, modelId) {
  return (
    check?.member_verdicts?.find((m) => m.model_id === modelId)?.verdict ??
    null
  );
}

/** A suspect nobody has answered about yet. */
export function isOpenSuspect(check, modelId) {
  return (check?.suspects ?? []).some(
    (s) => s.model_id === modelId && !s.verdict,
  );
}

/** Shown when a save is refused: the question stays open beside it. */
export const SAVE_ERROR = "Could not save your answer. Try again.";

/** Words for the owner's set verdict. */
export function setVerdictText(verdict) {
  return verdict === "yes"
    ? "this set produces sensible output"
    : "this set does not produce sensible output";
}

/** Words for the owner's verdict on one member. */
export function memberVerdictText(verdict, name) {
  return verdict === "not_problem"
    ? `${name} is not a problem in this set`
    : `${name} is a problem in this set`;
}

/**
 * The mark a model's own shelf row wears, from the row's `set_verdicts`. A
 * cross wins over a check; the tooltip lists both when both exist.
 *
 * @returns {{verdict: "problem"|"not_problem", tooltip: string}|null}
 */
export function modelVerdictMark(setVerdicts) {
  const marks = setVerdicts ?? [];
  const sentence = (kind, label) => {
    const hits = marks.filter((m) => m.verdict === kind);
    if (!hits.length) return "";
    const n = hits.length === 1 ? "1 set" : `${hits.length} sets`;
    return `${label} in ${n}: ${hits.map((m) => m.names.join(" + ")).join("; ")}`;
  };
  const problem = sentence("problem", "Marked a problem");
  const fine = sentence("not_problem", "Not a problem");
  if (!problem && !fine) return null;
  return {
    verdict: problem ? "problem" : "not_problem",
    tooltip: [problem, fine].filter(Boolean).join(". "),
  };
}
