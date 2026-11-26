<template>
  <AppDialog
    :open="open"
    :title="`More angles of ${location?.name ?? 'the location'}`"
    subtitle="New views of exactly this place, from one of its pictures"
    size="lg"
    :persistent="working"
    @close="close"
  >
    <div class="la" data-testid="location-angles">
      <template v-if="phase === 'describe'">
        <section class="la-section">
          <FieldLabel>From this picture</FieldLabel>
          <div class="la-sources" role="radiogroup" aria-label="The picture to start from">
            <button
              v-for="(pid, i) in location?.picture_ids ?? []"
              :key="pid"
              type="button"
              role="radio"
              class="la-source"
              :class="{ 'la-source--on': source === pid }"
              :aria-checked="source === pid ? 'true' : 'false'"
              :aria-label="`Picture ${i + 1}`"
              @click="source = pid"
            >
              <img :src="pictureThumbnailUrl(pid)" alt="" />
            </button>
          </div>
        </section>
        <AppTextarea
          v-model="layout"
          label="Where things are, seen from this picture"
          :rows="3"
          placeholder="The room has exactly one open fireplace, in a brick chimney breast on the left wall; a long oak bar runs along the right wall; tall windows at the far end."
        />
        <p class="la-hint">
          Say it once, and say "exactly one" for anything there is one of, so it is
          not drawn twice.
        </p>
        <section class="la-section">
          <FieldLabel>New views</FieldLabel>
          <div v-for="(shot, i) in shots" :key="i" class="la-shot">
            <AppSelect
              v-model="shot.preset"
              class="la-shot__preset"
              compact
              hide-label
              :label="`View ${i + 1}`"
              :options="PRESETS"
              :data-testid="`la-preset-${i}`"
            />
            <AppInput
              v-model="shot.words"
              class="la-shot__words"
              :aria-label="`What view ${i + 1} shows`"
              :placeholder="PLACEHOLDERS[shot.preset]"
            />
          </div>
        </section>
        <AppInput v-model="light" label="Light" placeholder="plain daylight" />
      </template>

      <template v-else-if="phase === 'working'">
        <div class="la-working" role="status">
          <span class="la-spin" aria-hidden="true"></span>
          <span>{{ status }}</span>
        </div>
        <p class="la-hint">
          About three minutes: a short video of the place first, then each new
          view restored from it. Closing stops it.
        </p>
      </template>

      <template v-else-if="phase === 'pick'">
        <p class="la-hint">
          Keep the views that look right. Each was taken from the video and
          restored at full size; a view that drifted or turned back into the
          picture you started from is marked.
        </p>
        <div class="la-views">
          <label
            v-for="view in views"
            :key="view.restored_id"
            class="la-view"
            :class="{ 'la-view--bad': !view.ok }"
          >
            <img :src="pictureThumbnailUrl(view.restored_id)" alt="" />
            <span class="la-view__foot">
              <input v-model="kept" type="checkbox" :value="view.restored_id" />
              {{ view.ok ? view.name : `${view.name} · ${view.reason}` }}
            </span>
          </label>
        </div>
        <p class="la-hint">The video goes into {{ location?.name }} too, as an establishing shot.</p>
      </template>

      <p v-if="error" class="la-error" role="alert">{{ error }}</p>
    </div>

    <template #footer>
      <!-- Cancel stops a draw, but must not walk away from a half-done Use. -->
      <AppButton variant="secondary" :disabled="working && phase === 'pick'" @click="close">{{
        phase === "pick" ? "Discard" : "Cancel"
      }}</AppButton>
      <AppButton
        v-if="phase === 'describe'"
        variant="primary"
        :disabled="!source || !ready"
        data-testid="la-draw"
        @click="draw"
        >Draw {{ shots.length }} new views</AppButton
      >
      <AppButton
        v-else-if="phase === 'pick'"
        variant="primary"
        :loading="working"
        data-testid="la-use"
        @click="use"
        >Use {{ kept.length }} {{ kept.length === 1 ? "view" : "views" }}</AppButton
      >
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * More angles… for a location (#2819): new views of exactly this place, from
 * one of its pictures. One *MiniMax H3 Location Shots* run films static shots
 * of it joined by hard cuts; the server takes the sharpest frame of each new
 * shot; each frame is restored with *MiniMax H3 Restore a Frame* (the frame
 * as the start, the picture as the second reference), and the server checks
 * each restored view against its frame. The kept views and the video go into
 * the location's set; the frames and the discarded views go to the Scrapheap.
 *
 * ponytail: the runs are followed while the dialog is open; closing stops
 * them. Keep a draw across a close (as the sheet dialog does) if wanted.
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import {
  buildLocationShotsPrompt,
  checkLocationShots,
  takeLocationShots,
} from "../../api/characterSheets";
import { bulkAddPicturesToSet } from "../../api/pictureSets";
import {
  deletePictures,
  pictureThumbnailUrl,
  restoreScrapheap,
} from "../../api/pictures";
import {
  getRunOutcome,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
  stopRun,
} from "../../api/workflows";
import { errorMessage } from "../../utils/apiError";
import { runKey } from "../../utils/gpuMemory";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The Studio's location: `{id, name, description, picture_ids}`. */
  location: { type: Object, default: null },
});
const emit = defineEmits(["close", "done"]);

/** The built-ins this draws with, by the names they ship under. */
const SHOTS_WORKFLOW = "MiniMax H3 Location Shots (PixlStash)";
const RESTORE_WORKFLOW = "MiniMax H3 Restore a Frame (PixlStash)";
const PRESETS = [
  { label: "Closer", value: "closer" },
  { label: "Opposite side", value: "opposite" },
  { label: "Reverse", value: "reverse" },
  { label: "Left", value: "left" },
  { label: "Right", value: "right" },
];
const PRESET_NAMES = Object.fromEntries(PRESETS.map((p) => [p.value, p.label]));
const PLACEHOLDERS = {
  closer: "the fireplace",
  opposite: "the bar along the far wall",
  reverse: "the fireplace on the right wall, the bar on the left",
  left: "what is to the left",
  right: "what is to the right",
};
const POLL_MS = 3000;

const phase = ref("describe");
const source = ref(null);
const layout = ref("");
const light = ref("plain daylight");
const shots = ref([]);
const status = ref("");
const error = ref("");
const working = ref(false);
const views = ref([]);
const kept = ref([]);
let videoId = null;
let frameIds = [];
let liveKeys = [];
let alive = true;
// Bumped by a close: a draw started before it stops following its runs.
let drawing = 0;

const ready = computed(() => shots.value.length > 0);

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    phase.value = "describe";
    source.value = props.location?.picture_ids?.[0] ?? null;
    layout.value = "";
    light.value = "plain daylight";
    shots.value = [
      { preset: "closer", words: "" },
      { preset: "opposite", words: "" },
      { preset: "reverse", words: "" },
    ];
    error.value = "";
    views.value = [];
    kept.value = [];
    videoId = null;
    frameIds = [];
    liveKeys = [];
  },
  { immediate: true },
);

function close() {
  drawing += 1;
  for (const key of liveKeys) {
    stopRun(key).catch((e) => console.warn("Couldn't stop a More angles run", key, e));
  }
  liveKeys = [];
  working.value = false;
  emit("close");
}

onBeforeUnmount(() => {
  alive = false;
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** The workflow shipped under *name*, or an error saying it is missing. */
function builtIn(cards, name) {
  const card = cards.find((c) => c.name === name);
  if (!card) {
    throw new Error(
      `${name} is not in Workflows. It ships with PixlStash; if it was removed, restore it from the built-in workflows.`,
    );
  }
  return card;
}

/** A workflow's picture inputs a run can fill, as its pre-flight names them. */
async function inputsOf(workflowId) {
  const check = await preflightWorkflowRun(
    { workflow_id: workflowId, count: 1, prompt: "a place" },
    { cached: true },
  );
  const groups = check?.groups || [];
  const reasons = groups.flatMap((g) => g.reasons || []).filter((r) => r.code !== "picture_input_unfilled");
  if (reasons.length) {
    throw new Error(reasons[0].message || "The workflow can't run as it is. Open it from Workflows to see why.");
  }
  return groups.flatMap((g) => g.picture_inputs || []).filter((i) => i.mode !== "fixed");
}

/** Start one run and give back its key. */
async function start(body) {
  const answer = await runWorkflowCard(body);
  const key = (answer?.prompts || []).map((p) => runKey(p)).find(Boolean);
  if (!key) throw new Error(answer?.error || "The workflow can't run as it is.");
  liveKeys.push(key);
  return key;
}

/** Wait for every run in *keys* to end; their pictures, in run order. */
async function finished(keys) {
  const mine = drawing;
  const done = new Map();
  // Lost answers in a row, per run: one blip costs each run one miss.
  const misses = new Map();
  while (alive && mine === drawing && done.size < keys.length) {
    for (const key of keys) {
      if (done.has(key)) continue;
      let outcome;
      try {
        outcome = await getRunOutcome(key);
        misses.delete(key);
      } catch (e) {
        // One lost answer must not throw away minutes of drawing.
        const missed = (misses.get(key) ?? 0) + 1;
        if (missed >= 3) throw e;
        misses.set(key, missed);
        continue;
      }
      if (outcome.status === "failed") {
        throw new Error(outcome.message || "A run failed.");
      }
      if (outcome.status === "completed") done.set(key, outcome.picture_ids || []);
    }
    if (done.size < keys.length) await sleep(POLL_MS);
  }
  if (!alive || mine !== drawing) throw new Error("Stopped.");
  liveKeys = liveKeys.filter((key) => !keys.includes(key));
  return keys.map((key) => done.get(key));
}

async function draw() {
  if (!source.value || working.value) return;
  working.value = true;
  error.value = "";
  phase.value = "working";
  try {
    status.value = "Getting the workflows ready…";
    const { cards } = await listWorkflowCards();
    const shotsCard = builtIn(cards, SHOTS_WORKFLOW);
    const restoreCard = builtIn(cards, RESTORE_WORKFLOW);
    const [shotInputs, restoreInputs] = await Promise.all([
      inputsOf(shotsCard.id),
      inputsOf(restoreCard.id),
    ]);
    if (!shotInputs.length || restoreInputs.length < 2) {
      throw new Error("The More angles workflows have lost their picture inputs. Restore them from the built-in workflows.");
    }
    const { prompt } = await buildLocationShotsPrompt({
      description: props.location?.description || props.location?.name || "",
      layout: layout.value,
      light: light.value,
      shots: shots.value,
    });

    status.value = "Filming the place…";
    const videoKey = await start({
      workflow_id: shotsCard.id,
      prompt,
      inputs: [{ ...pick(shotInputs[0]), picture_id: source.value }],
      count: 1,
      label: `More angles · ${props.location?.name}`,
    });
    const [videoPictures] = await finished([videoKey]);
    videoId = videoPictures.at(-1);
    if (videoId == null) throw new Error("The video run made nothing.");

    status.value = "Taking the new views out of the video…";
    ({ picture_ids: frameIds } = await takeLocationShots(videoId, shots.value.length));

    status.value = `Restoring ${frameIds.length} views…`;
    // By the node's title: the slot label is an address, not a name.
    const frameSlot = restoreInputs.find((i) => /frame/i.test(i.title || "")) ?? restoreInputs[0];
    const originalSlot = restoreInputs.find((i) => i !== frameSlot);
    const restoreKeys = [];
    for (const frameId of frameIds) {
      restoreKeys.push(
        await start({
          workflow_id: restoreCard.id,
          inputs: [
            { ...pick(frameSlot), picture_id: frameId },
            { ...pick(originalSlot), picture_id: source.value },
          ],
          count: 1,
          hold: true,
          label: `More angles · ${props.location?.name}`,
        }),
      );
    }
    const restored = (await finished(restoreKeys)).map((ids) => ids.at(-1));
    if (restored.some((id) => id == null)) throw new Error("A restore run made nothing.");

    status.value = "Checking each view…";
    const { views: checked } = await checkLocationShots(
      source.value,
      frameIds.map((frameId, i) => ({ frame_id: frameId, restored_id: restored[i] })),
    );
    views.value = checked.map((view, i) => ({
      ...view,
      name: PRESET_NAMES[shots.value[i]?.preset] ?? `View ${i + 1}`,
    }));
    kept.value = checked.filter((v) => v.ok).map((v) => v.restored_id);
    phase.value = "pick";
  } catch (e) {
    // A run that failed leaves the others drawing: stop them too.
    for (const key of liveKeys) {
      stopRun(key).catch((err) => console.warn("Couldn't stop a More angles run", key, err));
    }
    liveKeys = [];
    if (!alive || !props.open) return;
    console.warn("More angles failed", props.location?.id, e);
    error.value = `Couldn't draw more angles. ${errorMessage(e, "Please try again.")}`;
    phase.value = "describe";
  } finally {
    working.value = false;
  }
}

const pick = (input) => ({ slot_label: input.slot_label, input_name: input.input_name });

async function use() {
  if (working.value) return;
  working.value = true;
  error.value = "";
  try {
    if (kept.value.length) await restoreScrapheap([...kept.value]);
    await bulkAddPicturesToSet(props.location.id, [videoId, ...kept.value]);
    if (frameIds.length) await deletePictures(frameIds);
    emit("done", kept.value.length);
    emit("close");
  } catch (e) {
    error.value = `Couldn't put the views in ${props.location?.name}. ${errorMessage(e, "Please try again.")}`;
  } finally {
    working.value = false;
  }
}
</script>

<style scoped>
.la {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.la-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.la-sources {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.la-source {
  width: 96px;
  height: 64px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
  cursor: pointer;
}

.la-source img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.la-source--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}

.la-source:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--space-1);
}

.la-shot {
  display: flex;
  gap: var(--space-2);
}

.la-shot__preset {
  flex: 0 0 160px;
}

.la-shot__words {
  flex: 1;
  min-width: 0;
}

.la-hint {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.la-working {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: 120px;
  justify-content: center;
  font-size: var(--text-sm);
}

.la-spin {
  width: var(--space-5);
  height: var(--space-5);
  border-radius: var(--radius-pill);
  border: 2px solid rgba(var(--v-theme-on-surface), 0.2);
  border-top-color: rgb(var(--v-theme-on-surface));
  animation: la-turn 0.9s linear infinite;
}

@keyframes la-turn {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .la-spin {
    animation: none;
  }
}

.la-views {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}

.la-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  cursor: pointer;
}

.la-view img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border-radius: var(--radius-sm);
}

.la-view__foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.la-view--bad .la-view__foot {
  color: rgb(var(--v-theme-warning));
}

.la-error {
  margin: 0;
  color: rgb(var(--v-theme-error));
  font-size: var(--text-sm);
}
</style>
