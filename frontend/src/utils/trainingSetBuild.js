// A training set still being built (`PictureSet.building`): what its sidebar
// row says, which of its sheet runs are still going, and the name a new one
// takes. The build screen (views/TrainingSetBuild.vue) writes `building`; the
// sidebar, Tasks and App read it through these.

import { runKey } from "./gpuMemory";

/**
 * "<name> training set", or "<name> training set 2" and on when taken: the
 * name the dialog used to propose. Case-folded, as the backend compares.
 *
 * @param {string} name - the character's name
 * @param {Array<{name: string}>} sets - every picture set
 * @returns {string}
 */
export function freeTrainingSetName(name, sets) {
  const base = `${name} training set`;
  const taken = new Set((sets || []).map((s) => String(s?.name || "").toLowerCase()));
  let candidate = base;
  for (let n = 2; taken.has(candidate.toLowerCase()); n += 1) {
    candidate = `${base} ${n}`;
  }
  return candidate;
}

/**
 * The build's sheet runs still in flight, as the Tasks store lists them.
 *
 * @param {object|null} building - a set's `building`
 * @param {Array<object>} liveRuns - the Tasks store's `comfyuiRuns`
 * @returns {Array<object>} the live runs, in the store's shape
 */
export function liveBuildRuns(building, liveRuns) {
  const keys = new Set(
    [
      ...(building?.run_keys || []),
      ...(building?.sheets?.runs || []).map((r) => r.key),
    ].filter(Boolean),
  );
  return (liveRuns || []).filter((run) => keys.has(runKey(run)));
}

/**
 * What a set's sidebar row says in place of its count while it is built:
 * not drawn yet, drawing (with the drawing run's percent), drawn, a failed run,
 * picking. Null for a finished set.
 *
 * @param {object|null} building - the set's `building`
 * @param {Array<object>} liveRuns - the Tasks store's `comfyuiRuns`
 * @returns {{text: string, percent: ?number, error: boolean}|null}
 */
export function buildLine(building, liveRuns) {
  if (!building) return null;
  const line = (text, extra = {}) => ({ text, percent: null, error: false, ...extra });
  if (building.stage !== "generate") {
    const l = building.line;
    return line(l ? `picking · ${l.picked} of ${l.passing}` : "picking");
  }
  const runs = building.sheets?.runs || [];
  if (!runs.length) return line("not drawn yet");
  const live = liveBuildRuns(building, liveRuns);
  if (live.length) {
    const drawing = live.find((r) => r.status === "running");
    const at = runs.length - live.length + 1;
    return line(`drawing ${at} of ${runs.length}`, {
      percent: drawing ? Math.round(Number(drawing.percent) || 0) : null,
    });
  }
  const failed = runs.filter((r) => r.status === "failed").length;
  if (failed) {
    return line(`${failed} of ${runs.length} failed`, { error: true });
  }
  return line(`${runs.length} drawn · keep one`);
}

/**
 * The set still being built whose sheet draws include *runKeys* or drew
 * *pictureIds*: what a Character sheet row in Tasks opens.
 *
 * @param {Array<object>} sets - every picture set, with its `building`
 * @param {{runKeys?: string[], pictureIds?: number[]}} run
 * @returns {object|null} the set's row
 */
export function setOfSheetRun(sets, { runKeys = [], pictureIds = [] }) {
  return (
    (sets || []).find((set) => {
      const sheets = set?.building?.sheets;
      if (!sheets) return false;
      const keys = new Set([
        ...(sheets.run_history || []),
        ...(sheets.runs || []).map((r) => r.key),
      ]);
      const drawn = new Set(sheets.drawn_ids || []);
      return (
        runKeys.some((key) => keys.has(key)) ||
        pictureIds.some((id) => drawn.has(id))
      );
    }) || null
  );
}

const MAN_WORDS =
  /\b(man|men|male|boy|guy|gentleman|husband|father|he|him|his|himself)\b/gi;
const WOMAN_WORDS =
  /\b(woman|women|female|girl|lady|wife|mother|she|her|hers|herself)\b/gi;

/**
 * Who a character sheet draws, read from the description: "man" or "woman"
 * when its words lean one way, null when they say nothing or both equally.
 *
 * @param {string} text - the description
 * @returns {"man"|"woman"|null}
 */
export function subjectFromDescription(text) {
  const men = (String(text || "").match(MAN_WORDS) || []).length;
  const women = (String(text || "").match(WOMAN_WORDS) || []).length;
  if (men === women) return null;
  return men > women ? "man" : "woman";
}
