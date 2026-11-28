// What the library holds, read for the Guide's greying (#2825).
//
// Every need is library content, answered by an endpoint that already exists
// (docs/guide/concepts.md, "Prerequisites"). A missing tool (ComfyUI,
// AI-toolkit, an MCP client) is never a need. The three needs with no count
// yet (a person with a LoRA, text in a picture, a share of a person, set or
// project) have no key here at all: the owner ruled those tasks stay lit.
import { listCharacters } from "../api/characters";
import { listGroups } from "../api/dedup";
import { listLibraries } from "../api/libraries";
import { getPendingMoves } from "../api/moves";
import { getUndoState } from "../api/operations";
import { getPictureCount } from "../api/pictures";
import { listPictureSets } from "../api/pictureSets";
import { listSnapshots } from "../api/snapshots";
import { getStudioOverview } from "../api/studio";
import { listTrainingJobs } from "../api/training";
import { listWorkflowCards } from "../api/workflows";
import { VIDEO_EXTENSIONS } from "../utils/media";

const count = async (query = "") =>
  Number((await getPictureCount(query))?.count ?? 0);

const videoQuery = () => {
  const params = new URLSearchParams();
  for (const ext of VIDEO_EXTENSIONS) params.append("format", ext.toUpperCase());
  return params.toString();
};

const list = (value) => (Array.isArray(value) ? value : []);

/**
 * One reading per endpoint, each answering one or more need keys. A reading
 * that fails answers nothing: its needs read as unknown, which the caller
 * treats as met (greying is a hint, so an unreadable library must not look
 * empty) after logging which reading failed.
 */
const READINGS = [
  async () => {
    const n = await count();
    return { picture: n >= 1, pictures2: n >= 2 };
  },
  async () => ({ face: (await count("face_filter=with_face")) >= 1 }),
  async () => ({ video: (await count(videoQuery())) >= 1 }),
  async () => ({ stack: (await count("stack_state=stacked")) >= 1 }),
  async () => ({ shared: (await count("shared_only=true")) >= 1 }),
  async () => {
    const people = list(
      await listCharacters({ params: { include_counts: true } }),
    );
    return {
      person: people.length >= 1,
      personWithPictures: people.some((p) => Number(p?.image_count) > 0),
    };
  },
  async () => {
    const sets = list(await listPictureSets()).filter(
      (s) => !s?.reference_character,
    );
    return { set: sets.length >= 1, sets2: sets.length >= 2 };
  },
  async () => {
    const { cards } = await listWorkflowCards({ includeOneOffs: true });
    return {
      recipe: cards.some((c) => c?.manual === false),
      ownWorkflow: cards.some((c) => c?.built_in === false),
      outpaintWorkflow: cards.some((c) => c?.type === "outpaint"),
    };
  },
  async () => ({
    duplicates: list((await listGroups({ limit: 1 }))?.groups).length > 0,
  }),
  async () => ({ pendingMoves: list(await getPendingMoves()).length > 0 }),
  async () => ({ trainingRun: list(await listTrainingJobs()).length > 0 }),
  async () => ({
    libraries2: list((await listLibraries())?.libraries).length >= 2,
  }),
  async () => ({ snapshot: list(await listSnapshots()).length > 0 }),
  async () => ({ history: (await getUndoState())?.can_undo === true }),
  async () => {
    const studio = await getStudioOverview();
    return {
      location: list(studio?.locations).length > 0,
      scene: list(studio?.scenes).length > 0,
    };
  },
];

/**
 * Read every need. Resolves to `{ key: true|false }` for each need it could
 * read; a key missing from the answer could not be read.
 */
export async function readNeeds() {
  const results = await Promise.allSettled(READINGS.map((read) => read()));
  const met = {};
  results.forEach((result, i) => {
    if (result.status === "fulfilled") Object.assign(met, result.value);
    else
      console.warn(
        `Guide: library reading ${i} failed; its needs read as met.`,
        result.reason,
      );
  });
  return met;
}

/**
 * Whether `needs` (a concept's or a task's) is met. An element that is an
 * array is met when ANY of its keys is. A key that could not be read counts as
 * met. Returns the first unmet key, or null when everything is met.
 */
export function firstUnmet(needs, met) {
  const ok = (key) => met?.[key] !== false;
  for (const need of needs ?? []) {
    if (Array.isArray(need) ? !need.some(ok) : !ok(need))
      return Array.isArray(need) ? need[0] : need;
  }
  return null;
}
