<template>
  <AppDialog
    :open="open"
    :title="isNew ? 'New location' : `More angles of ${location?.name ?? 'the location'}`"
    :subtitle="
      isNew
        ? 'A wide picture of the place, then more views of exactly it, filmed from that picture'
        : 'New views of exactly this place, from one of its pictures'
    "
    size="2xl"
    :persistent="working || zoomedId != null"
    @close="close"
  >
    <div class="la" data-testid="location-angles">
      <template v-if="phase === 'place'">
        <AppInput v-model="name" label="Name" placeholder="New location" data-testid="la-name" />
        <AppTextarea
          v-model="description"
          label="The place"
          :rows="3"
          placeholder="a narrow trail through a snowy pine forest, a fallen log across the path"
          data-testid="la-description"
        />
        <p class="la-hint">
          First {{ WIDE_COUNT }} wide pictures of it to choose from, then the other views
          are filmed from the one you choose, so they all show the same place. Always
          empty, in plain daylight.
        </p>
      </template>

      <template v-else-if="phase === 'describe'">
        <section class="la-section">
          <FieldLabel>{{ isNew ? "Start from the picture that looks right" : "From this picture" }}</FieldLabel>
          <div class="la-sources" role="radiogroup" aria-label="The picture to start from">
            <div v-for="(pid, i) in sources" :key="pid" class="la-tile">
              <button
                type="button"
                role="radio"
                class="la-source"
                :class="{ 'la-source--on': source === pid }"
                :aria-checked="source === pid ? 'true' : 'false'"
                :aria-label="`Picture ${i + 1}`"
                :aria-keyshortcuts="'Z'"
                :data-testid="`la-source-${i}`"
                @click="source = pid"
                @dblclick="openZoom(pid)"
                @wheel="onTileWheel($event, pid)"
                @keydown.z.exact="openZoom(pid)"
              >
                <img :src="pictureThumbnailUrl(pid)" alt="" />
                <span v-if="source === pid" class="la-source__ck" aria-hidden="true"
                  ><v-icon size="14">mdi-check</v-icon></span
                >
              </button>
              <!-- Zoom has its own control: a click on the picture chooses it. -->
              <button
                type="button"
                class="la-mag"
                :data-source="pid"
                :aria-label="`Zoom in on picture ${i + 1}`"
                data-testid="la-zoom-open"
                @click="openZoom(pid)"
              >
                <v-icon size="16">mdi-magnify-plus-outline</v-icon>
              </button>
            </div>
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
            <div class="la-shot__words">
              <BaseModelInput
                v-model="shot.words"
                class="tbm-input"
                :options="things"
                :list-label="`In this picture`"
                open-on-focus
                :aria-label="`What view ${i + 1} shows`"
                :placeholder="PLACEHOLDERS[shot.preset]"
                maxlength="500"
                :data-testid="`la-words-${i}`"
              />
            </div>
          </div>
          <p class="la-hint" data-testid="la-things">
            {{
              thingsLoading
                ? "Looking at what is in this picture…"
                : things.length
                  ? "Type what each view shows, or pick from what is in this picture."
                  : "Type what each view shows."
            }}
          </p>
        </section>
        <label class="la-section">
          <FieldLabel>Light</FieldLabel>
          <BaseModelInput
            v-model="light"
            class="tbm-input"
            :options="LIGHTS"
            list-label="Light"
            :max-shown="LIGHTS.length"
            open-on-focus
            placeholder="plain daylight"
            maxlength="200"
            data-testid="la-light"
          />
        </label>
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
        <p v-if="views.length !== shots.length" class="la-hint" data-testid="la-count">
          The video made {{ views.length }} of the {{ shots.length }} views asked for: the
          video model sometimes runs two shots together. Draw again for more.
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
        <p class="la-hint">
          <template v-if="isNew">
            {{ placeName }} is made with the picture you started from, the views you keep and
            the video, as an establishing shot.
          </template>
          <template v-else>The video goes into {{ location?.name }} too, as an establishing shot.</template>
        </p>
      </template>

      <p v-if="error" class="la-error" role="alert">{{ error }}</p>
    </div>

    <ZoomPopout
      :open="zoomedId != null"
      :src="zoomedId != null ? fullUrl(zoomedId) : ''"
      :alt="zoomedId != null ? `Picture ${zoomPos + 1}` : ''"
      :caption="zoomedId != null ? `Picture ${zoomPos + 1} of ${sources.length}` : ''"
      :has-prev="hasPrev"
      :has-next="hasNext"
      :entry="zoomEntry"
      @close="closeZoom"
      @prev="stepZoom(-1)"
      @next="stepZoom(1)"
    >
      <template #actions>
        <AppButton
          v-if="zoomedId != null"
          :variant="source === zoomedId ? 'secondary' : 'primary'"
          :disabled="source === zoomedId"
          data-testid="la-zoom-choose"
          @click="source = zoomedId"
          >{{ source === zoomedId ? "Chosen" : "Start from this one" }}</AppButton
        >
      </template>
    </ZoomPopout>

    <template #footer>
      <!-- Cancel stops a draw, but must not walk away from a half-done Use. -->
      <AppButton variant="secondary" :disabled="working && phase === 'pick'" @click="close">{{
        phase === "pick" ? "Discard" : "Cancel"
      }}</AppButton>
      <AppButton
        v-if="phase === 'place'"
        tooltip="Draw the place as four views on one picture instead"
        data-testid="la-sheet"
        @click="emit('sheet')"
        >Draw a 2×2 sheet instead</AppButton
      >
      <AppButton
        v-if="phase === 'place'"
        variant="primary"
        :disabled="!description.trim()"
        data-testid="la-wide"
        @click="drawWide"
        >Draw {{ WIDE_COUNT }} wide pictures</AppButton
      >
      <AppButton
        v-else-if="phase === 'describe'"
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
import BaseModelInput from "../widgets/BaseModelInput.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import ZoomPopout from "../widgets/ZoomPopout.vue";
import { useZoomPopout } from "../../composables/useZoomPopout.js";
import {
  buildLocationShotsPrompt,
  buildSheetPrompt,
  checkLocationShots,
  locationPictureThings,
  takeLocationShots,
} from "../../api/characterSheets";
import { bulkAddPicturesToSet, createPictureSet } from "../../api/pictureSets";
import { listSetInStudio } from "../../api/studio";
import {
  deletePictures,
  listPicturesByIds,
  pictureMediaUrl,
  pictureThumbnailUrl,
  restoreScrapheap,
} from "../../api/pictures";
import {
  getRunOutcome,
  getWorkflowCard,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
  stopRun,
} from "../../api/workflows";
import { errorMessage } from "../../utils/apiError";
import { runKey } from "../../utils/gpuMemory";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The Studio's location: `{id, name, description, picture_ids}`. None
   * makes a new location: its wide pictures are drawn first (*Draw location
   * sheets…*), and *Use* makes its set. */
  location: { type: Object, default: null },
});
const emit = defineEmits(["close", "done", "sheet"]);

/** The built-ins this draws with, by the names they ship under. */
const SHOTS_WORKFLOW = "MiniMax H3 Location Shots (PixlStash)";
const RESTORE_WORKFLOW = "MiniMax H3 Restore a Frame (PixlStash)";
const WIDE_WORKFLOW = "MiniMax H3 Image T2I (PixlStash)";
const WIDE_COUNT = 4;
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
const LIGHTS = [
  "plain daylight",
  "soft overcast daylight",
  "bright midday sun, hard shadows",
  "golden hour sun, long warm shadows",
  "blue hour dusk, lamps just on",
  "night, warm lamplight",
  "night, moonlight through the windows",
  "candlelight",
  "cold fluorescent light",
  "neon signs at night",
];
const POLL_MS = 3000;

const phase = ref("describe"); // place (new only) | describe | working | pick
const source = ref(null);
const name = ref("");
const description = ref("");
// A new location's wide pictures, held in the Scrapheap until one is used.
const candidates = ref([]);
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
// A new location's set once made: a Use that failed after it is retried
// into the same set rather than making a second one under a taken name.
let madeSet = null;
// A new location's wide pictures drawn from, back from the Scrapheap: a run
// takes only a kept picture as an input. Sent back unless *Use* keeps one.
let broughtBack = new Set();
let liveKeys = [];
let alive = true;
// Bumped by a close: a draw started before it stops following its runs.
let drawing = 0;

const ready = computed(() => shots.value.length > 0);
const isNew = computed(() => !props.location);
const sources = computed(() => props.location?.picture_ids ?? candidates.value);
const placeName = computed(
  () => props.location?.name ?? (name.value.trim() || "New location"),
);
const placeDescription = computed(() =>
  isNew.value
    ? description.value.trim()
    : props.location?.description || props.location?.name || "",
);

// The pictures to start from, zoomed full size: the shared pop-out, stepping
// through them in their order. Closing puts focus back on the zoom control.
const sourceList = computed(() => sources.value);
const {
  zoomedId,
  zoomEntry,
  zoomPos,
  hasPrev,
  hasNext,
  openZoom,
  onTileWheel,
  stepZoom,
  closeZoom,
} = useZoomPopout(sourceList, (id) =>
  document.querySelector(`[data-testid="la-zoom-open"][data-source="${id}"]`)?.focus(),
);
watch(phase, () => {
  zoomedId.value = null;
});

// What each picture holds, as Florence-2 sees it, offered as what a view
// shows; read again for each picture chosen, kept per picture.
// A picture that could not be read holds nothing, so it is not asked again.
const thingsOf = ref({});
const asking = new Set();
const things = computed(() => thingsOf.value[source.value] ?? []);
const thingsLoading = computed(() => source.value != null && !(source.value in thingsOf.value));
watch(source, async (id) => {
  if (id == null || id in thingsOf.value || asking.has(id)) return;
  asking.add(id);
  let found = [];
  try {
    ({ things: found } = await locationPictureThings(id));
  } catch (e) {
    console.warn("Couldn't read what is in the picture; the views are typed by hand", id, e);
  } finally {
    asking.delete(id);
    thingsOf.value = { ...thingsOf.value, [id]: found || [] };
  }
}, { immediate: true });

// Each picture's format, for its full-size file; the thumbnail until known.
const formats = ref({});
watch(sources, async (ids) => {
  if (!ids.length) return;
  try {
    // A new location's pictures are held in the Scrapheap until one is used.
    const rows = await listPicturesByIds(ids, { fields: "grid", onlyDeleted: isNew.value });
    formats.value = Object.fromEntries((rows || []).map((r) => [r.id, r.format]));
  } catch (e) {
    console.warn("Couldn't read the pictures' formats; zooming the thumbnails", ids, e);
  }
}, { immediate: true });

function fullUrl(id) {
  const format = formats.value[id];
  return format ? pictureMediaUrl(id, format) : pictureThumbnailUrl(id);
}

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    phase.value = isNew.value ? "place" : "describe";
    source.value = props.location?.picture_ids?.[0] ?? null;
    name.value = "";
    description.value = "";
    candidates.value = [];
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
    madeSet = null;
    broughtBack = new Set();
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
  sendBack([...broughtBack]);
  emit("close");
}

/** Wide pictures brought back to be drawn from but not used: to the Scrapheap. */
function sendBack(ids) {
  broughtBack = new Set([...broughtBack].filter((id) => !ids.includes(id)));
  if (!ids.length) return;
  deletePictures(ids).catch((e) =>
    console.warn("Couldn't send unused wide pictures back to the Scrapheap", ids, e),
  );
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

/** Start a run and give back its keys, one per picture asked for. */
async function startAll(body) {
  const answer = await runWorkflowCard(body);
  const keys = (answer?.prompts || []).map((p) => runKey(p)).filter(Boolean);
  if (!keys.length) throw new Error(answer?.error || "The workflow can't run as it is.");
  liveKeys.push(...keys);
  return keys;
}

/** Start one run and give back its key. */
async function start(body) {
  return (await startAll(body))[0];
}

/** A new location: draw its wide pictures, then choose one and its views. */
async function drawWide() {
  if (working.value || !description.value.trim()) return;
  working.value = true;
  error.value = "";
  phase.value = "working";
  try {
    status.value = "Getting the workflow ready…";
    const { cards } = await listWorkflowCards();
    const card = builtIn(cards, WIDE_WORKFLOW);
    const [{ card: full }, wide] = await Promise.all([
      getWorkflowCard(card.id),
      buildSheetPrompt({ kind: "location", description: description.value.trim(), single: true }),
    ]);
    const sizes = (full?.defaults || []).filter((d) => ["width", "height"].includes(d.input_name));
    if (!sizes.length) throw new Error(`${WIDE_WORKFLOW} has no picture size to set.`);
    status.value = `Drawing ${WIDE_COUNT} wide pictures…`;
    const keys = await startAll({
      workflow_id: card.id,
      prompt: wide.prompt,
      negative: wide.negative,
      values: sizes.map((d) => ({
        slot_label: d.slot_label,
        input_name: d.input_name,
        value: d.input_name === "width" ? wide.width : wide.height,
      })),
      count: WIDE_COUNT,
      // Not the library's until one is used: straight to the Scrapheap.
      hold: true,
      label: `New location · ${placeName.value}`,
    });
    candidates.value = (await finished(keys)).map((ids) => ids.at(-1)).filter((id) => id != null);
    if (!candidates.value.length) throw new Error("The runs made no picture.");
    source.value = candidates.value[0];
    phase.value = "describe";
  } catch (e) {
    for (const key of liveKeys) {
      stopRun(key).catch((err) => console.warn("Couldn't stop a wide picture run", key, err));
    }
    liveKeys = [];
    if (!alive || !props.open) return;
    console.warn("Drawing a new location's wide pictures failed", e);
    error.value = `Couldn't draw the wide pictures. ${errorMessage(e, "Please try again.")}`;
    phase.value = "place";
  } finally {
    working.value = false;
  }
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
    if (isNew.value && !broughtBack.has(source.value)) {
      await restoreScrapheap([source.value]);
      broughtBack.add(source.value);
    }
    const { prompt } = await buildLocationShotsPrompt({
      description: placeDescription.value,
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
      label: `More angles · ${placeName.value}`,
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
          label: `More angles · ${placeName.value}`,
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
    // A shot short or extra and the names no longer line up with the rows:
    // which one the model ran together is not known, so number them.
    const named = checked.length === shots.value.length;
    views.value = checked.map((view, i) => ({
      ...view,
      name: (named && PRESET_NAMES[shots.value[i]?.preset]) || `View ${i + 1}`,
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

/** A new location's set: the picture started from, the kept views and the
 * video. Made first, so a name already taken stops before anything moves;
 * the other wide pictures stay in the Scrapheap. */
async function useNew() {
  if (!madeSet) {
    try {
      ({ picture_set: madeSet } = await createPictureSet({
        name: placeName.value,
        description: placeDescription.value,
      }));
    } catch (e) {
      error.value = `Couldn't make the set ${placeName.value}. ${errorMessage(e, "Please try again.")} Discard and start again with another name.`;
      return;
    }
  }
  const set = madeSet;
  const ids = [source.value, videoId, ...kept.value];
  await restoreScrapheap([source.value, ...kept.value]);
  await bulkAddPicturesToSet(set.id, ids);
  // The set is the location: the Studio lists it as one (#2819), once it
  // holds the pictures, so a Use that fails leaves no empty location there.
  try {
    await listSetInStudio(set.id, "location");
  } catch (e) {
    console.warn("Couldn't list the new location in the Studio", set.id, e);
  }
  if (frameIds.length) await deletePictures(frameIds);
  broughtBack.delete(source.value);
  sendBack([...broughtBack]);
  emit("done", kept.value.length, set, ids);
  emit("close");
}

async function use() {
  if (working.value) return;
  working.value = true;
  error.value = "";
  try {
    if (isNew.value) {
      await useNew();
      return;
    }
    if (kept.value.length) await restoreScrapheap([...kept.value]);
    await bulkAddPicturesToSet(props.location.id, [videoId, ...kept.value]);
    if (frameIds.length) await deletePictures(frameIds);
    emit("done", kept.value.length);
    emit("close");
  } catch (e) {
    error.value = `Couldn't put the views in ${placeName.value}. ${errorMessage(e, "Please try again.")}`;
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

/* Four across the 2xl dialog: each about eight times the area of the old
   96x64 tile, big enough to judge before zooming. */
.la-sources {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.la-tile {
  position: relative;
}

/* The chosen tile carries its ring on its own border and a check mark that
   is added, not restyled: a box-shadow change alone was not repainted until
   the window lost and regained focus. */
.la-source {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  padding: 0;
  overflow: hidden;
  border: 2px solid transparent;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  cursor: pointer;
}

.la-source img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.la-source--on {
  border-color: var(--active-bar);
}

.la-source:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--space-1);
}

.la-source__ck {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

/* The sheet step's zoom control (.cs-mag), on these tiles. */
.la-mag {
  position: absolute;
  right: var(--space-3);
  bottom: var(--space-3);
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: zoom-in;
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
}

.la-tile:hover .la-mag,
.la-mag:focus-visible,
.la-source:focus-visible + .la-mag {
  opacity: 1;
}

.la-mag:hover,
.la-mag:focus-visible {
  background: var(--scrim-photo-strong);
}

.la-mag:hover {
  box-shadow: 0 0 0 2px rgb(var(--v-theme-on-dark-surface));
}

@media (hover: none) {
  .la-mag {
    opacity: 1;
  }
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
