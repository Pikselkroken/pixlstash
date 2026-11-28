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
    :persistent="zoomedId != null || (working && phase === 'pick')"
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
        <AppTextarea
          v-model="widePrompt"
          label="Prompt for the wide pictures"
          :rows="4"
          placeholder="Written from the place when you draw. Write it now to change it first."
          data-testid="la-prompt"
        />
        <div class="la-prompt-tools">
          <AppButton
            size="sm"
            :disabled="!description.trim() || embellishing"
            data-testid="la-write"
            @click="writePromptHere"
            >Write it from the place</AppButton
          >
          <AppButton
            v-if="llm"
            size="sm"
            icon-left="auto-fix"
            :loading="embellishing"
            :disabled="!description.trim() && !widePrompt.trim()"
            :tooltip="llmTip"
            data-testid="la-embellish"
            @click="embellish"
            >Embellish</AppButton
          >
        </div>
      </template>

      <template v-else-if="phase === 'describe'">
        <section class="la-section">
          <FieldLabel>{{ isNew ? "Start from the picture that looks right" : "From these pictures" }}</FieldLabel>
          <div
            class="la-sources"
            :role="isNew ? 'radiogroup' : 'group'"
            :aria-label="isNew ? 'The picture to start from' : 'The pictures to film from'"
          >
            <div v-for="(pid, i) in sources" :key="pid" class="la-tile">
              <button
                type="button"
                :role="isNew ? 'radio' : 'checkbox'"
                class="la-source"
                :class="{ 'la-source--on': source === pid || lastSource === pid }"
                :aria-checked="source === pid || lastSource === pid ? 'true' : 'false'"
                :aria-label="`Picture ${i + 1}${source === pid ? ', the start' : lastSource === pid ? ', the end' : ''}`"
                :aria-keyshortcuts="'Z'"
                :data-testid="`la-source-${i}`"
                @click="pickSource(pid, $event)"
                @dblclick="openZoom(pid)"
                @wheel="onTileWheel($event, pid)"
                @keydown.z.exact="openZoom(pid)"
              >
                <img :src="pictureThumbnailUrl(pid)" alt="" />
                <span v-if="source === pid && !lastSource" class="la-source__ck" aria-hidden="true"
                  ><v-icon size="14">mdi-check</v-icon></span
                >
                <span v-else-if="source === pid" class="la-source__end" aria-hidden="true">Start</span>
                <span v-else-if="lastSource === pid" class="la-source__end" aria-hidden="true">End</span>
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
        <p
          v-if="isNew && candidates.length < WIDE_COUNT"
          class="la-hint"
          data-testid="la-wide-short"
        >
          {{ candidates.length }} of the {{ WIDE_COUNT }} wide pictures came back; the
          other runs made nothing. Draw them again below for more.
        </p>
        <details v-if="isNew" class="la-redo" data-testid="la-redo">
          <summary>None looks right? Change the prompt and draw them again</summary>
          <AppTextarea
            v-model="widePrompt"
            label="Prompt for the wide pictures"
            :rows="4"
            data-testid="la-prompt-again"
          />
          <div class="la-prompt-tools">
            <AppButton
              v-if="llm"
              size="sm"
              icon-left="auto-fix"
              :loading="embellishing"
              :disabled="!widePrompt.trim()"
              :tooltip="llmTip"
              data-testid="la-embellish-again"
              @click="embellish"
              >Embellish</AppButton
            >
            <AppButton
              size="sm"
              icon-left="refresh"
              :disabled="!widePrompt.trim() || embellishing"
              data-testid="la-wide-again"
              @click="drawWide"
              >Draw {{ WIDE_COUNT }} again</AppButton
            >
          </div>
        </details>
        <p v-if="!isNew && sources.length > 1" class="la-hint" data-testid="la-sources-hint">
          Click a picture to film from it. Ctrl+click a second to end the video on
          it: the new views are filmed between the two, so both hold the place.
        </p>
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
          <div class="la-count-row">
            <FieldLabel>New views</FieldLabel>
            <Segmented
              :model-value="shots.length"
              :options="COUNT_OPTIONS"
              aria-label="How many new views"
              data-testid="la-view-count"
              @update:model-value="setViewCount"
            />
            <span class="la-hint">{{ videoSeconds }} s of video, two seconds a view</span>
          </div>
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
        <div class="la-progress" aria-hidden="true">
          <span :style="{ width: `${Math.round(fraction * 100)}%` }"></span>
        </div>
        <p class="la-hint">
          Close this and it goes on in the background: the Studio shows how far
          it has got under the location, and it is in Tasks. Stop ends it.
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
        <!-- The picture they were filmed from leads the row, so each view is
             judged against it; the zoom steps from it through the views. -->
        <div class="la-views">
          <div class="la-view la-view--original" data-testid="la-original">
            <div class="la-tile">
              <img :src="pictureThumbnailUrl(source)" alt="" @dblclick="openZoom(source)" />
              <button
                type="button"
                class="la-mag"
                :data-source="source"
                aria-label="Zoom in on the picture you started from"
                data-testid="la-zoom-open"
                @click="openZoom(source)"
              >
                <v-icon size="16">mdi-magnify-plus-outline</v-icon>
              </button>
            </div>
            <span class="la-view__foot">Started from</span>
          </div>
          <div v-if="lastSource" class="la-view la-view--original" data-testid="la-original-end">
            <div class="la-tile">
              <img :src="pictureThumbnailUrl(lastSource)" alt="" @dblclick="openZoom(lastSource)" />
              <button
                type="button"
                class="la-mag"
                :data-source="lastSource"
                aria-label="Zoom in on the picture the video ended on"
                data-testid="la-zoom-open"
                @click="openZoom(lastSource)"
              >
                <v-icon size="16">mdi-magnify-plus-outline</v-icon>
              </button>
            </div>
            <span class="la-view__foot">Ended on</span>
          </div>
          <div
            v-for="view in views"
            :key="view.restored_id"
            class="la-view"
            :class="{ 'la-view--bad': !view.ok }"
          >
            <div class="la-tile">
              <img
                :src="pictureThumbnailUrl(view.restored_id)"
                alt=""
                @dblclick="openZoom(view.restored_id)"
              />
              <button
                type="button"
                class="la-mag"
                :data-source="view.restored_id"
                :aria-label="`Zoom in on ${view.name}`"
                data-testid="la-zoom-open"
                @click="openZoom(view.restored_id)"
              >
                <v-icon size="16">mdi-magnify-plus-outline</v-icon>
              </button>
            </div>
            <label class="la-view__foot">
              <input v-model="kept" type="checkbox" :value="view.restored_id" />
              {{ view.ok ? view.name : `${view.name} · ${view.reason}` }}
            </label>
          </div>
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
      :alt="zoomedId != null ? zoomCaption : ''"
      :caption="zoomedId != null ? zoomCaption : ''"
      :has-prev="hasPrev"
      :has-next="hasNext"
      :entry="zoomEntry"
      @close="closeZoom"
      @prev="stepZoom(-1)"
      @next="stepZoom(1)"
    >
      <template #actions>
        <AppButton
          v-if="zoomedId != null && phase === 'pick' && zoomedId !== source && zoomedId !== lastSource"
          :variant="kept.includes(zoomedId) ? 'secondary' : 'primary'"
          data-testid="la-zoom-keep"
          @click="toggleKept(zoomedId)"
          >{{ kept.includes(zoomedId) ? "Kept · drop it" : "Keep this view" }}</AppButton
        >
        <AppButton
          v-else-if="zoomedId != null && phase === 'describe'"
          :variant="source === zoomedId ? 'secondary' : 'primary'"
          :disabled="source === zoomedId"
          data-testid="la-zoom-choose"
          @click="pickSource(zoomedId)"
          >{{ source === zoomedId ? "Chosen" : "Start from this one" }}</AppButton
        >
      </template>
    </ZoomPopout>

    <template #footer>
      <!-- A draw goes on when this closes: Stop ends it, Discard throws away
           what it made. Neither walks away from a half-done Use. -->
      <template v-if="phase === 'working'">
        <AppButton variant="secondary" data-testid="la-stop" @click="stop">Stop</AppButton>
        <AppButton variant="primary" data-testid="la-background" @click="close"
          >Keep going in the background</AppButton
        >
      </template>
      <AppButton
        v-else-if="phase === 'pick' || held"
        variant="secondary"
        :disabled="working"
        data-testid="la-discard"
        @click="discard"
        >Discard</AppButton
      >
      <AppButton v-else variant="secondary" @click="close">Cancel</AppButton>
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
import Segmented from "../widgets/Segmented.vue";
import ZoomPopout from "../widgets/ZoomPopout.vue";
import { useZoomPopout } from "../../composables/useZoomPopout.js";
import {
  buildLocationShotsPrompt,
  buildSheetPrompt,
  checkLocationShots,
  embellishPrompt,
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
  exportWorkflow,
  getRunOutcome,
  getWorkflowCard,
  getWorkflowFormInputs,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
  stopRun,
} from "../../api/workflows";
import { errorMessage } from "../../utils/apiError";
import { useLocationDrawStore } from "../../stores/useLocationDrawStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTasksStore } from "../../stores/useTasksStore";
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
// The same, ending on a second picture (`last_frame`).
const SHOTS_FIRST_LAST_WORKFLOW = "MiniMax H3 Location Shots, First and Last (PixlStash)";
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
// New views asked of one video: the server's limit, and the video grows two
// seconds a view (the picture's own shot included), as does its VRAM.
const MAX_VIEWS = 4;
const SHOT_SECONDS = 2;
const COUNT_OPTIONS = Array.from({ length: MAX_VIEWS }, (_, i) => ({
  id: i + 1,
  label: String(i + 1),
  testid: `la-view-count-${i + 1}`,
}));
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
// Text encoders that are chat LLMs, by their loader's type: ComfyUI's Generate
// Text node can run them (MiniMax H3: Qwen3-VL; Qwen-Image, HunyuanImage,
// OmniGen2, LongCat: Qwen2.5-VL; Z-Image and Lumina 2: Qwen3 or Gemma; FLUX.2:
// Mistral or Qwen3).
const LLM_LOADERS = new Set(["CLIPLoader", "CLIPLoaderGGUF"]);
const LLM_TYPES = new Set([
  "minimax",
  "qwen_image",
  "hunyuan_image",
  "omnigen2",
  "longcat_image",
  "lumina2",
  "flux2",
  "ovis",
]);

const phase = ref("describe"); // place (new only) | describe | working | pick
const source = ref(null);
// A second picture the video ends on (More angles only), or null.
const lastSource = ref(null);
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
// The wide pictures' prompt: written from the place, the owner's to change.
const widePrompt = ref("");
// The LLM the wide-picture workflow encodes with, when it is one:
// `{ loader: {class_type, inputs}, name }`, else null.
const llm = ref(null);
const embellishing = ref(false);
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
const drawStore = useLocationDrawStore();
const noticeStore = useNoticeStore();
const tasks = useTasksStore();
// How far the draw under way has got, 0-1.
const fraction = ref(0);
// Something drawn that closing must keep: a draw under way, views to pick,
// or a new location's wide pictures to choose from.
const held = computed(
  () => working.value || phase.value === "pick" || (isNew.value && candidates.value.length > 0),
);

/** Say how far the draw has got, here and under its location in the Studio. */
function report(text, at, extra = {}) {
  status.value = text;
  fraction.value = at;
  drawStore.report({
    locationId: props.location?.id ?? null,
    name: placeName.value,
    status: text,
    fraction: at,
    ready: false,
    ...extra,
  });
}

/** The draw is over: ready for the owner, or gone. Said when nobody saw it. */
function settle(text, { level = "success" } = {}) {
  if (props.open) return;
  noticeStore.push({ level, text });
}
const videoSeconds = computed(() => SHOT_SECONDS * (shots.value.length + 1));

/** As many view rows as *n*: kept rows keep their words; a new one takes the
 * first preset no row has yet. */
function setViewCount(n) {
  const rows = shots.value.slice(0, n);
  while (rows.length < n) {
    const used = new Set(rows.map((r) => r.preset));
    const preset = PRESETS.find((p) => !used.has(p.value))?.value ?? "closer";
    rows.push({ preset, words: "" });
  }
  shots.value = rows;
}
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

// Zoomed full size in the shared pop-out, stepping through them in order: the
// pictures to start from, or at the end the one started from and its views,
// so each view is compared with it. Closing refocuses the zoom control.
const sourceList = computed(() =>
  phase.value === "pick"
    ? [source.value, ...(lastSource.value ? [lastSource.value] : []), ...views.value.map((v) => v.restored_id)]
    : sources.value,
);
const zoomCaption = computed(() => {
  if (phase.value !== "pick") return `Picture ${zoomPos.value + 1} of ${sources.value.length}`;
  if (zoomedId.value === source.value) return "The picture you started from";
  if (zoomedId.value === lastSource.value) return "The picture the video ended on";
  const view = views.value.find((v) => v.restored_id === zoomedId.value);
  return view ? `${view.name}${view.ok ? "" : ` · ${view.reason}`}` : "";
});

function toggleKept(id) {
  kept.value = kept.value.includes(id) ? kept.value.filter((k) => k !== id) : [...kept.value, id];
}
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
async function readFormats(ids, onlyDeleted) {
  if (!ids.length) return;
  try {
    const rows = await listPicturesByIds(ids, { fields: "grid", onlyDeleted });
    formats.value = {
      ...formats.value,
      ...Object.fromEntries((rows || []).map((r) => [r.id, r.format])),
    };
  } catch (e) {
    console.warn("Couldn't read the pictures' formats; zooming the thumbnails", ids, e);
  }
}
// A new location's pictures are held in the Scrapheap until one is used, as
// every restored view is until it is kept.
watch(sources, (ids) => readFormats(ids, isNew.value), { immediate: true });
watch(views, (list) => readFormats(list.map((v) => v.restored_id), true));

function fullUrl(id) {
  const format = formats.value[id];
  return format ? pictureMediaUrl(id, format) : pictureThumbnailUrl(id);
}

watch(
  () => props.open,
  (open) => {
    // A draw under way, or what it made, is what the dialog opens back on.
    if (!open || held.value) return;
    phase.value = isNew.value ? "place" : "describe";
    source.value = props.location?.picture_ids?.[0] ?? null;
    lastSource.value = null;
    name.value = "";
    description.value = "";
    candidates.value = [];
    widePrompt.value = "";
    if (isNew.value && !llm.value) findLlm();
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

/** Close the dialog. A draw under way goes on, and what it made waits. */
function close() {
  if (held.value) {
    emit("close");
    return;
  }
  discard();
}

/** Stop the draw under way: its runs end, the frames it took out of its
 * video go to the Scrapheap (they are pictures of the library until a Use
 * deletes them), and the dialog is back where it was. */
function stop() {
  drawing += 1;
  for (const key of liveKeys) {
    stopRun(key).catch((e) => console.warn("Couldn't stop a More angles run", key, e));
  }
  liveKeys = [];
  if (frameIds.length) {
    const frames = frameIds;
    deletePictures(frames).catch((e) =>
      console.warn("Couldn't send a stopped draw's frames to the Scrapheap", frames, e),
    );
    frameIds = [];
  }
  working.value = false;
  error.value = "";
  phase.value = isNew.value && !candidates.value.length ? "place" : "describe";
  drawStore.report(
    isNew.value && candidates.value.length
      ? { status: "Wide pictures to choose from", fraction: 1, ready: true }
      : null,
  );
}

// Another session: the draw's runs stop, and what it made is let go without
// a word to this library, whose ids those pictures are not.
watch(
  () => drawStore.epoch,
  () => {
    // What it made is let go first, so the stop has nothing to report back
    // into the store the reset has just emptied, nor frames to delete in a
    // library they are not of.
    frameIds = [];
    candidates.value = [];
    views.value = [];
    kept.value = [];
    // The LLM is another library's workflow's: found again on the next open.
    llm.value = null;
    broughtBack = new Set();
    madeSet = null;
    stop();
    phase.value = isNew.value ? "place" : "describe";
    drawStore.report(null);
  },
);

/** Throw away what this draw made, stop what is still running, and close. */
function discard() {
  stop();
  candidates.value = [];
  views.value = [];
  sendBack([...broughtBack]);
  drawStore.report(null);
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

/** The loader of the LLM the wide-picture workflow encodes its prompt with. */
async function findLlm() {
  try {
    const { cards } = await listWorkflowCards();
    const card = cards.find((c) => c.name === WIDE_WORKFLOW);
    if (!card) return;
    const { workflow } = await exportWorkflow(card.id);
    const node = Object.values(workflow || {}).find(
      (n) =>
        LLM_LOADERS.has(n?.class_type) &&
        LLM_TYPES.has(n?.inputs?.type) &&
        typeof n?.inputs?.clip_name === "string",
    );
    if (!node) return;
    const inputs = Object.fromEntries(
      ["clip_name", "type", "device"]
        .filter((k) => typeof node.inputs[k] === "string")
        .map((k) => [k, node.inputs[k]]),
    );
    llm.value = { loader: { class_type: node.class_type, inputs }, name: inputs.clip_name };
  } catch (e) {
    console.warn("Couldn't find an LLM in the wide-picture workflow; no Embellish", e);
  }
}

const llmTip = computed(() =>
  llm.value
    ? `Rewrites the prompt richer with ${llm.value.name}, the LLM ${WIDE_WORKFLOW} reads its prompt with. Every fact in it is kept.`
    : "",
);

/** The server's prompt for one wide picture of the place, into the field. */
async function writePrompt() {
  const wide = await buildSheetPrompt({
    kind: "location",
    description: description.value.trim(),
    single: true,
  });
  widePrompt.value = wide.prompt;
  return wide;
}

/** *Write it from the place*: the same, its failure said on the error line. */
async function writePromptHere() {
  error.value = "";
  try {
    await writePrompt();
  } catch (e) {
    console.warn("Writing the wide pictures' prompt failed", e);
    error.value = `Couldn't write the prompt. ${errorMessage(e, "Please try again.")}`;
  }
}

async function embellish() {
  if (!llm.value || embellishing.value) return;
  embellishing.value = true;
  error.value = "";
  try {
    if (!widePrompt.value.trim()) await writePrompt();
    const { prompt } = await embellishPrompt(llm.value.loader, widePrompt.value);
    if (alive) widePrompt.value = prompt;
  } catch (e) {
    console.warn("Embellishing the wide pictures' prompt failed", llm.value?.name, e);
    error.value = `Couldn't embellish the prompt. ${errorMessage(e, "Please try again.")}`;
  } finally {
    embellishing.value = false;
  }
}

/** A new location: draw its wide pictures, then choose one and its views.
 * Again from the pictures step: four new ones in place of the four shown. */
async function drawWide() {
  if (working.value || !description.value.trim()) return;
  const again = candidates.value.length > 0;
  working.value = true;
  error.value = "";
  phase.value = "working";
  try {
    report("Getting the workflow ready…", 0, { count: WIDE_COUNT, making: "wide" });
    const { cards } = await listWorkflowCards();
    const card = builtIn(cards, WIDE_WORKFLOW);
    const [{ card: full }, wide] = await Promise.all([
      getWorkflowCard(card.id),
      buildSheetPrompt({ kind: "location", description: description.value.trim(), single: true }),
    ]);
    // The owner's prompt when they wrote or changed one; shown either way.
    const prompt = widePrompt.value.trim() || wide.prompt;
    widePrompt.value = prompt;
    const sizes = (full?.defaults || []).filter((d) => ["width", "height"].includes(d.input_name));
    if (!sizes.length) throw new Error(`${WIDE_WORKFLOW} has no picture size to set.`);
    const drawingWide = `Drawing ${WIDE_COUNT} wide pictures…`;
    report(drawingWide, 0.02);
    const keys = await startAll({
      workflow_id: card.id,
      prompt,
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
    const drawn = (await finished(keys, (f) => report(drawingWide, 0.02 + 0.98 * f)))
      .map((ids) => ids.at(-1))
      .filter((id) => id != null);
    if (!drawn.length) throw new Error("The runs made no picture.");
    // The four shown before stay in the Scrapheap; one drawn from goes back.
    sendBack([...broughtBack]);
    candidates.value = drawn;
    source.value = candidates.value[0];
    phase.value = "describe";
    report("Wide pictures to choose from", 1, { ready: true });
    settle(`The wide pictures of ${placeName.value} are ready to choose from.`);
  } catch (e) {
    for (const key of liveKeys) {
      stopRun(key).catch((err) => console.warn("Couldn't stop a wide picture run", key, err));
    }
    liveKeys = [];
    if (!alive || e?.message === "Stopped.") return;
    console.warn("Drawing a new location's wide pictures failed", e);
    error.value = `Couldn't draw the wide pictures. ${errorMessage(e, "Please try again.")}`;
    phase.value = again ? "describe" : "place";
    drawStore.report(again ? { status: "Wide pictures to choose from", fraction: 1, ready: true } : null);
    settle(`${error.value}`, { level: "error" });
  } finally {
    working.value = false;
  }
}

/** How far *keys* have got together, 0-1: an ended run whole, a running one
 * by the percent ComfyUI reports for it in Tasks. */
function runsFraction(keys, done) {
  const percent = new Map(tasks.comfyuiRuns.map((run) => [runKey(run), Number(run.percent) || 0]));
  const sum = keys.reduce((n, key) => n + (done.has(key) ? 1 : (percent.get(key) ?? 0) / 100), 0);
  return keys.length ? sum / keys.length : 1;
}

/** Wait for every run in *keys* to end; their pictures, in run order.
 * *onProgress* hears how far they have got, 0-1, at every poll. */
async function finished(keys, onProgress = null) {
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
    // A stop while this poll was out must not hear one report more.
    if (mine === drawing) onProgress?.(runsFraction(keys, done));
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
    report("Getting the workflows ready…", 0, { count: shots.value.length, making: "views" });
    const { cards } = await listWorkflowCards();
    const endsOnPicture = lastSource.value != null;
    const shotsCard = builtIn(cards, endsOnPicture ? SHOTS_FIRST_LAST_WORKFLOW : SHOTS_WORKFLOW);
    const restoreCard = builtIn(cards, RESTORE_WORKFLOW);
    const [shotInputs, restoreInputs, length] = await Promise.all([
      inputsOf(shotsCard.id),
      inputsOf(restoreCard.id),
      durationOf(shotsCard.id),
    ]);
    if (!shotInputs.length || (endsOnPicture && shotInputs.length < 2) || restoreInputs.length < 2) {
      throw new Error("The More angles workflows have lost their picture inputs. Restore them from the built-in workflows.");
    }
    if (isNew.value && !broughtBack.has(source.value)) {
      await restoreScrapheap([source.value]);
      broughtBack.add(source.value);
    }
    const { prompt, seconds } = await buildLocationShotsPrompt({
      description: placeDescription.value,
      layout: layout.value,
      light: light.value,
      shots: shots.value,
      ...(endsOnPicture ? { last_picture: true } : {}),
    });

    report("Filming the place…", 0.02);
    const videoKey = await start({
      workflow_id: shotsCard.id,
      prompt,
      inputs: endsOnPicture ? firstAndLast(shotInputs) : [{ ...pick(shotInputs[0]), picture_id: source.value }],
      // Two seconds a shot: more views, a longer video, not shorter shots.
      ...(length ? { values: [{ ...pick(length), value: seconds }] } : {}),
      count: 1,
      label: `More angles · ${placeName.value}`,
    });
    const [videoPictures] = await finished([videoKey], (f) =>
      report("Filming the place…", 0.02 + 0.58 * f),
    );
    videoId = videoPictures.at(-1);
    if (videoId == null) throw new Error("The video run made nothing.");

    report("Taking the new views out of the video…", 0.6);
    ({ picture_ids: frameIds } = await takeLocationShots(
      videoId,
      shots.value.length,
      endsOnPicture,
    ));

    const restoring = `Restoring ${frameIds.length} views…`;
    report(restoring, 0.62);
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
    const restored = (
      await finished(restoreKeys, (f) => report(restoring, 0.62 + 0.36 * f))
    ).map((ids) => ids.at(-1));
    if (restored.some((id) => id == null)) throw new Error("A restore run made nothing.");

    report("Checking each view…", 0.98);
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
    report(`${views.value.length} new views to pick`, 1, { ready: true });
    settle(`The new views of ${placeName.value} are ready to pick.`);
  } catch (e) {
    // A run that failed leaves the others drawing: stop them too.
    for (const key of liveKeys) {
      stopRun(key).catch((err) => console.warn("Couldn't stop a More angles run", key, err));
    }
    liveKeys = [];
    if (!alive || e?.message === "Stopped.") return;
    console.warn("More angles failed", props.location?.id, e);
    error.value = `Couldn't draw more angles. ${errorMessage(e, "Please try again.")}`;
    phase.value = "describe";
    drawStore.report(
      isNew.value && candidates.value.length
        ? { status: "Wide pictures to choose from", fraction: 1, ready: true }
        : null,
    );
    settle(error.value, { level: "error" });
  } finally {
    working.value = false;
  }
}

const pick = (input) => ({ slot_label: input.slot_label, input_name: input.input_name });

/** A click picks the picture to film from; Ctrl+click (More angles) a second
 * the video ends on, and again takes it away. A new location's wide pictures
 * are takes of a place, not views of one, so there it is one. */
function pickSource(pid, event) {
  const adding = !isNew.value && (event?.ctrlKey || event?.metaKey);
  if (!adding) {
    source.value = pid;
    lastSource.value = null;
  } else if (pid === lastSource.value) {
    lastSource.value = null;
  } else if (pid === source.value) {
    source.value = lastSource.value ?? pid;
    lastSource.value = null;
  } else {
    lastSource.value = pid;
  }
}

/** The first-and-last shots workflow's two inputs, by their nodes' titles:
 * the one that names the last shot takes the second picture. */
function firstAndLast(inputs) {
  const last = inputs.find((i) => /last/i.test(i.title || "")) ?? inputs[1];
  const first = inputs.find((i) => i !== last);
  return [
    { ...pick(first), picture_id: source.value },
    { ...pick(last), picture_id: lastSource.value },
  ];
}

/** The shots workflow's duration in seconds, by its node's title, or null
 * when it has none to set (the video then keeps the length it ships with). */
async function durationOf(workflowId) {
  try {
    const { nodes } = await getWorkflowFormInputs(workflowId);
    for (const node of nodes || []) {
      if (!/duration/i.test(node.title || "")) continue;
      const value = (node.inputs || []).find((i) => i.input_name === "value");
      if (value) return value;
    }
    console.warn("The shots workflow has no duration to set; its video keeps its length", workflowId);
  } catch (e) {
    console.warn("Couldn't read the shots workflow's inputs; its video keeps its length", workflowId, e);
  }
  return null;
}

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
  // The location's from here: a Use that fails later and is then discarded
  // must not send the picture it is built on back to the Scrapheap. The kept
  // views go back with a Discard until they are in the set.
  broughtBack.delete(source.value);
  for (const id of kept.value) broughtBack.add(id);
  await bulkAddPicturesToSet(set.id, ids);
  for (const id of kept.value) broughtBack.delete(id);
  // The set is the location: the Studio lists it as one (#2819), once it
  // holds the pictures, so a Use that fails leaves no empty location there.
  try {
    await listSetInStudio(set.id, "location");
  } catch (e) {
    console.warn("Couldn't list the new location in the Studio", set.id, e);
  }
  if (frameIds.length) await deletePictures(frameIds);
  sendBack([...broughtBack]);
  candidates.value = [];
  views.value = [];
  phase.value = "place";
  drawStore.report(null);
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
    // Back to the Scrapheap with a Discard until they are in the location.
    for (const id of kept.value) broughtBack.add(id);
    await bulkAddPicturesToSet(props.location.id, [videoId, ...kept.value]);
    for (const id of kept.value) broughtBack.delete(id);
    if (frameIds.length) await deletePictures(frameIds);
    // A view kept by a Use that failed, then dropped before this one.
    sendBack([...broughtBack]);
    views.value = [];
    phase.value = "describe";
    drawStore.report(null);
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

.la-source__end {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: 20px;
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

.la-count-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
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

/* How far the draw has got: the sheet dialog's run bar. */
.la-progress {
  height: 4px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.la-progress > span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-primary));
  transition: width var(--dur-3) var(--ease-standard);
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
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.la-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.la-view__foot {
  cursor: pointer;
}

.la-view--original .la-view__foot {
  cursor: default;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.la-prompt-tools {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.la-redo {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.la-redo > summary {
  cursor: pointer;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.la-redo[open] > summary {
  margin-bottom: var(--space-2);
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
