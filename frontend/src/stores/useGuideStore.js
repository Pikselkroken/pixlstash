import { defineStore } from "pinia";
import { computed, onScopeDispose, ref } from "vue";
import { patchUserConfig } from "../api/config";
import { CONCEPTS, NEEDS, WELCOME_EDITION } from "../guide/catalogue";
import { firstUnmet, readNeeds } from "../guide/needs";
import { isReadOnly, onSessionReset } from "../utils/apiClient";

// The Guide (#2825): the catalogue in docs/guide/concepts.md, greyed by what
// the library holds, its welcome, and the wizard that walks a task on the
// real screen.
//
// Greying is a hint, never a lock: a greyed concept opens like any other and
// nothing in the Guide is disabled. It reads library content only; a missing
// tool (ComfyUI, AI-toolkit, an MCP client) greys nothing.

/** id → concept. */
export const CONCEPT_BY_ID = Object.fromEntries(CONCEPTS.map((c) => [c.id, c]));

/** The welcome's six slides, by the catalogue's `startup` order. */
export const STARTUP_CONCEPTS = CONCEPTS.filter(
  (c) => typeof c.startup === "number",
).sort((a, b) => a.startup - b.startup);

/** What the welcome shows on an empty library. */
export const EMPTY_LIBRARY_CONCEPTS = ["import-pictures", "generate-comfyui"];

export { WELCOME_EDITION };

/** Start here, by the catalogue's rules. */
export function startHereFor(met) {
  if (met.picture === false) return EMPTY_LIBRARY_CONCEPTS;
  if (met.person === false) return ["people", "similar-faces"];
  if (met.person === true) return ["consistent-characters", "studio"];
  return [];
}

/** The concept a need sends the reader to ("First: <name>"). */
export function firstConceptFor(needKey) {
  return CONCEPT_BY_ID[NEEDS[needKey]?.first] ?? null;
}

export const useGuideStore = defineStore("guide", () => {
  // ── What the library holds ──
  const met = ref({});
  const readAt = ref(0);
  let reading = null;
  // Bumped by a session change: a read that started before it is dropped
  // rather than written over the new library's (or token's) answer.
  let epoch = 0;

  /** Read the needs; a read already under way is shared. */
  function refresh() {
    const started = epoch;
    reading ??= readNeeds()
      .then((answer) => {
        if (started !== epoch) return;
        met.value = answer;
        readAt.value = Date.now();
      })
      .finally(() => {
        // After a reset a newer read may hold the slot: only clear our own.
        if (started === epoch) reading = null;
      });
    return reading;
  }

  /** Another library or credential: what the old one held is not news. */
  function reset() {
    epoch += 1;
    reading = null;
    met.value = {};
    readAt.value = 0;
    wizard.value = null;
    welcome.value = null;
    // The new session makes its own decision; a welcome already closed stays
    // closed, since "seen" is the user's.
    evaluated = false;
  }

  const startHere = computed(() =>
    readAt.value ? startHereFor(met.value) : [],
  );

  /** "start" | "normal" | "grey" for a concept. */
  function stateOf(concept) {
    if (startHere.value.includes(concept.id)) return "start";
    return firstUnmet(concept.needs, met.value) ? "grey" : "normal";
  }

  /** The need a concept or task is still missing, or null. */
  function missing(item) {
    return firstUnmet(item?.needs, met.value);
  }

  /** The Guide's search field; kept across the grid and a concept page. */
  const query = ref("");

  // ── The welcome ──
  const welcomeSeen = ref(null);
  const configLoaded = ref(false);
  /** null, or `{ concepts: [ids], withNew: bool }`. */
  const welcome = ref(null);
  let evaluated = false;

  function hydrate(cfg) {
    welcomeSeen.value =
      typeof cfg?.guide_welcome_seen === "string"
        ? cfg.guide_welcome_seen
        : null;
    configLoaded.value = true;
  }

  function slidesFor(empty) {
    return empty ? EMPTY_LIBRARY_CONCEPTS : STARTUP_CONCEPTS.map((c) => c.id);
  }

  /**
   * Once per session: on the first launch with pictures, or on the first
   * launch after a release added a New in 2.x headline. Never for a
   * read-only session, which could not record that it was seen.
   */
  async function evaluateWelcome({ libraryEmpty }) {
    if (evaluated || !configLoaded.value || isReadOnly.value) return;
    if (!WELCOME_EDITION) return;
    evaluated = true;
    const seen = welcomeSeen.value;
    if (seen === WELCOME_EDITION) return;
    const firstLaunch = seen === null;
    if (firstLaunch && libraryEmpty) return;
    const started = epoch;
    await refresh();
    // A session change (another library, a share token) while the needs were
    // read: that session decides its own welcome, if any.
    if (started !== epoch || isReadOnly.value) return;
    welcome.value = {
      concepts: slidesFor(met.value.picture === false),
      withNew: !firstLaunch,
    };
  }

  /** Play the welcome again (the Guide's toolbar). */
  async function replayWelcome() {
    const started = epoch;
    await refresh();
    if (started !== epoch) return;
    welcome.value = {
      concepts: slidesFor(met.value.picture === false),
      withNew: true,
    };
  }

  /** Esc, × or Not now: closed for this edition. */
  async function closeWelcome() {
    welcome.value = null;
    if (isReadOnly.value || welcomeSeen.value === WELCOME_EDITION) return;
    welcomeSeen.value = WELCOME_EDITION;
    try {
      await patchUserConfig({ guide_welcome_seen: WELCOME_EDITION });
    } catch (e) {
      // Kept in memory for this session; the next launch shows it again.
      console.error("Failed to save that the Guide's welcome was seen:", e);
    }
  }

  // ── The wizard ──
  /** null, or `{ conceptId, taskId, step }` (step is 0-based). */
  const wizard = ref(null);
  const wizardConcept = computed(() =>
    wizard.value ? CONCEPT_BY_ID[wizard.value.conceptId] : null,
  );
  const wizardTask = computed(
    () =>
      wizardConcept.value?.tasks.find((t) => t.id === wizard.value.taskId) ??
      null,
  );

  function startTask(conceptId, taskId) {
    const task = CONCEPT_BY_ID[conceptId]?.tasks.find((t) => t.id === taskId);
    if (!task?.steps?.length) return false;
    wizard.value = { conceptId, taskId, step: 0 };
    return true;
  }

  /** The next step, or the end after the last. */
  function nextStep() {
    if (!wizard.value || !wizardTask.value) return;
    if (wizard.value.step + 1 >= wizardTask.value.steps.length) endWizard();
    else wizard.value = { ...wizard.value, step: wizard.value.step + 1 };
  }

  function previousStep() {
    if (!wizard.value || wizard.value.step === 0) return;
    wizard.value = { ...wizard.value, step: wizard.value.step - 1 };
  }

  function endWizard() {
    wizard.value = null;
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    query,
    met,
    readAt,
    refresh,
    startHere,
    stateOf,
    missing,
    welcomeSeen,
    configLoaded,
    welcome,
    hydrate,
    evaluateWelcome,
    replayWelcome,
    closeWelcome,
    wizard,
    wizardConcept,
    wizardTask,
    startTask,
    nextStep,
    previousStep,
    endWizard,
  };
});
