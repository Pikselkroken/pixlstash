<template>
  <div class="csv" role="region" :aria-label="`${name} · Sheets`" data-testid="character-sheets">
    <!-- The shell's top band, the same 36px recipe as the grid's and the
         other destinations'. -->
    <div class="csv-toolbar">
      <AppButton
        variant="ghost"
        size="sm"
        icon-only
        icon-left="arrow-left"
        tooltip="Back"
        aria-label="Back"
        data-testid="cs-back"
        @click="goBack"
      />
      <h2 class="csv-title">Sheets</h2>
      <span class="csv-sub" data-testid="cs-sub">{{ subLine }}</span>
      <div class="csv-tb-right">
        <AppButton
          variant="primary"
          size="sm"
          icon-left="check"
          :loading="filing"
          :disabled="!canUse"
          data-testid="cs-use"
          @click="use"
        >
          {{ useLabel }}
        </AppButton>
        <TbGlobalActions separator />
      </div>
    </div>

    <div class="csv-body">
      <div ref="mainPane" class="csv-main" data-testid="cs-main">
        <p v-if="error" class="cs-notice cs-notice--bad" role="alert">
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span>{{ error }}</span>
        </p>
        <p
          v-if="mixedWarning"
          class="cs-notice cs-notice--warn"
          role="status"
          data-testid="cs-mixed"
        >
          <v-icon size="16">mdi-alert-outline</v-icon>
          <span>{{ mixedWarning }}</span>
        </p>
        <p v-if="filing" class="cs-notice" role="status">
          <v-icon size="16">mdi-content-cut</v-icon>
          <span>Cutting the sheets into pictures and reading their faces…</span>
        </p>
        <div v-if="filed" class="cs-notice cs-notice--ok" role="status" data-testid="cs-filed">
          <v-icon size="16">mdi-check-circle-outline</v-icon>
          <span
            >{{ filed.count }} {{ filed.count === 1 ? "picture" : "pictures" }} filed
            on {{ name }}. The sheets stay in the Scrapheap.</span
          >
          <button
            type="button"
            class="cs-link"
            data-testid="cs-create-set"
            @click="emit('create-training-set', { id: characterId, name })"
          >
            Create a training set
          </button>
        </div>

        <div v-if="loaded && !views.length" class="cs-empty" data-testid="cs-empty">
          <h3>No sheets for {{ name }} yet</h3>
          <span
            >Each sheet is one person drawn six times: four passport photos
            (front, three-quarter smiling, profile, three-quarter laughing) and
            two body photos. Describe {{ name }} on the right and draw; you pick
            the sheet that looks right.</span
          >
        </div>

        <section
          v-for="(v, index) in views"
          :key="v.draw.id"
          class="cs-draw"
          :class="{ 'cs-draw--past': index > 0 }"
          :data-draw="v.draw.id"
          data-testid="cs-draw"
        >
          <div class="cs-dhead">
            <h3 class="cs-secl">{{ v.heading }}</h3>
            <span
              v-if="v.percent != null"
              class="cs-dline"
              role="progressbar"
              aria-label="This draw"
              :aria-valuenow="v.percent"
              aria-valuemin="0"
              aria-valuemax="100"
              ><span :style="{ width: `${v.percent}%` }"
            /></span>
            <span v-if="v.percent != null" class="cs-dpct">{{ v.percent }}%</span>
            <AppButton
              variant="ghost"
              size="sm"
              icon-only
              icon-left="delete-outline"
              class="cs-ddel"
              :tooltip="`Delete this draw. Its sheets stay in the Scrapheap${v.draw.used.length ? ', its pictures on ' + name : ''}.`"
              :aria-label="`Delete the draw of ${v.when}`"
              :disabled="working || filing"
              data-testid="cs-draw-delete"
              @click="removeDraw(v.draw)"
            />
          </div>
          <template v-if="index === 0">
            <span class="cs-intro">{{ pickIntro(v) }}</span>
            <p v-if="v.noneHold" class="cs-notice cs-notice--warn" data-testid="cs-none-hold">
              <v-icon size="16">mdi-alert-outline</v-icon>
              <span
                >None of the {{ v.ended.length }} sheets shows one person in all
                six photos. Drawing more usually finds one. A shorter description
                with fewer competing details helps too.</span
              >
            </p>
            <p v-if="v.noneClose" class="cs-notice cs-notice--warn" data-testid="cs-none-close">
              <v-icon size="16">mdi-alert-outline</v-icon>
              <span>{{ v.noneClose }}</span>
            </p>
            <p v-if="v.failures.length" class="cs-notice cs-notice--bad" data-testid="cs-failed">
              <v-icon size="16">mdi-alert-circle-outline</v-icon>
              <span
                >{{ v.failures.length }} of the runs failed:
                {{ v.failures[0] }}</span
              >
            </p>
          </template>
          <ul class="cs-sheets" :class="{ 'cs-sheets--past': index > 0 }">
            <li v-for="slot in v.slots" :key="slot.key">
              <template v-if="slot.sheet">
                <div class="cs-tile">
                  <button
                    type="button"
                    class="cs-sheetimg"
                    :class="{ 'cs-sheetimg--on': isKept(slot.sheet) }"
                    :aria-pressed="isKept(slot.sheet)"
                    :aria-label="`Sheet ${slot.n}: ${sheetWords(slot.sheet)}`"
                    :aria-keyshortcuts="'Z'"
                    :disabled="sheetLocked(slot.sheet)"
                    data-testid="cs-sheet"
                    :data-sheet="slot.sheet.picture_id"
                    @click="toggleKeep(slot.sheet)"
                    @dblclick="openZoom(slot.sheet.picture_id)"
                    @wheel="onTileWheel($event, slot.sheet.picture_id)"
                    @keydown.z.exact="openZoom(slot.sheet.picture_id)"
                  >
                    <img
                      :src="pictureThumbnailUrl(slot.sheet.picture_id)"
                      alt=""
                      loading="lazy"
                    />
                    <span class="cs-sheetimg__rk">{{ slot.n }}</span>
                    <span
                      v-if="isKept(slot.sheet)"
                      class="cs-sheetimg__ck"
                      aria-hidden="true"
                      ><v-icon size="14">mdi-check</v-icon></span
                    >
                  </button>
                  <!-- Zoom has its own control: a click on the sheet ticks it. -->
                  <button
                    type="button"
                    class="cs-mag"
                    data-testid="cs-zoom-open"
                    :data-sheet="slot.sheet.picture_id"
                    :aria-label="`Zoom in on sheet ${slot.n}`"
                    @click="openZoom(slot.sheet.picture_id)"
                  >
                    <v-icon size="16">mdi-magnify-plus-outline</v-icon>
                  </button>
                </div>
                <span
                  v-if="index === 0 && slot.sheet.drawnFrom && slot.sheet.closeness != null"
                  class="cs-ccap"
                  data-testid="cs-closeness"
                >
                  <span class="cs-cline"
                    ><b>Sheet {{ slot.n }}</b
                    ><span class="cs-meter" aria-hidden="true"
                      ><span :style="{ width: meterWidth(slot.sheet) }" /></span
                    ><span class="cs-num">{{
                      slot.sheet.closeness.toFixed(2)
                    }}</span
                    ><span>{{ closeWords(slot.sheet) }}</span></span
                  >
                  <span class="cs-cap">{{ slot.sheet.agreement_words }}</span>
                </span>
                <span v-else-if="index === 0" class="cs-cap"
                  ><b>Sheet {{ slot.n }}</b> · {{ sheetWords(slot.sheet) }}</span
                >
              </template>
              <span
                v-else
                class="cs-sheetimg cs-sheetimg--wait"
                data-testid="cs-slot"
              >
                <span>{{ slot.label }}</span>
                <span
                  v-if="slot.percent != null"
                  class="cs-progress"
                  role="progressbar"
                  aria-label="Drawing"
                  :aria-valuenow="slot.percent"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  ><span :style="{ width: `${slot.percent}%` }"
                /></span>
              </span>
            </li>
          </ul>
        </section>
      </div>

      <!-- The inspector: what the next draw is made of. -->
      <aside class="csv-inspector" aria-label="Draw settings" data-testid="cs-rail">
        <p v-if="likenessNotice && !fromReference" class="cs-notice" data-testid="cs-likeness">
          <v-icon size="16">{{
            chosenLora ? "mdi-account-check-outline" : "mdi-alert-outline"
          }}</v-icon>
          <span>{{ likenessNotice }}</span>
        </p>
        <p
          v-if="workflowsLoaded && !workflowOptions.length"
          class="cs-notice cs-notice--bad"
          data-testid="cs-no-workflow"
        >
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span>{{
            fromReference
              ? "Sheets from a reference need a Qwen-Image-Edit sheet workflow, and the library has none."
              : "Sheets need a MOSS-Image text-to-image workflow, and the library has none. Import one from Workflows, then come back."
          }}</span>
        </p>

        <div v-if="referenceCards.length" class="cs-fld">
          <FieldLabel>Draw from</FieldLabel>
          <Segmented
            v-model="mode"
            aria-label="Draw from"
            :options="MODES"
            full
            data-testid="cs-mode"
          />
        </div>
        <template v-if="fromReference">
          <img
            v-if="referenceId"
            class="cs-cbig"
            :src="pictureThumbnailUrl(referenceId)"
            alt="The reference picture"
            data-testid="cs-reference"
          />
          <div class="cs-cstrip" role="group" aria-label="Reference picture">
            <button
              v-for="(id, index) in referenceIds"
              :key="id"
              type="button"
              class="cs-reft"
              :class="{ 'cs-reft--on': id === referenceId }"
              :aria-pressed="id === referenceId"
              :aria-label="`${name}'s ${ORDINALS[index]} reference face`"
              data-testid="cs-reference-choice"
              @click="referenceId = id"
            >
              <img :src="pictureThumbnailUrl(id)" alt="" />
            </button>
            <button
              ref="pickButton"
              type="button"
              class="cs-reft cs-reft--add"
              aria-label="Pick another picture"
              data-testid="cs-reference-pick"
              @click="pickerOpen = true"
            >
              <v-icon size="14">mdi-image-plus-outline</v-icon>
            </button>
          </div>
          <span class="cs-hint"
            >{{ referenceHint }}
            <button
              type="button"
              class="cs-link cs-link--inline"
              data-testid="cs-change-faces"
              @click="openFaces"
            >
              Change…
            </button></span
          >
          <p class="cs-notice cs-notice--warn" data-testid="cs-reference-warning">
            <v-icon size="16">mdi-alert-outline</v-icon>
            <span
              >These sheets are drawn to look like the person in the picture.
              Use a real person's picture only with their agreement.</span
            >
          </p>
        </template>
        <div v-else-if="convertible" class="cs-item" data-testid="cs-convert">
          <span
            >Sheets can also be drawn from a reference picture, with a copy of
            a Qwen-Image-Edit workflow that draws at the sheet's size.</span
          >
          <button
            type="button"
            class="cs-link cs-link--start"
            :disabled="working"
            @click="makeSheetWorkflow"
          >
            Make a sheet workflow from {{ convertible.name }}
          </button>
        </div>

        <div class="cs-fld">
          <AppTextarea
            v-model="form.description"
            label="Description"
            :rows="3"
            placeholder="Adult, hair, eyes, anything that makes the face theirs"
            data-testid="cs-description"
          />
          <span class="cs-hint">{{ descriptionHint }}</span>
        </div>
        <div class="cs-fld">
          <AppInput
            v-model="form.body"
            label="Body"
            placeholder="slim build · curvy with full hips · tall and athletic"
          />
          <span class="cs-hint"
            >Goes into every photo, the passport photos too: a different build
            draws a different face. Empty is an average build.</span
          >
        </div>
        <div class="cs-fld">
          <AppSelect
            v-model="loraSha"
            label="Character LoRA · optional"
            :options="loraOptions"
            data-testid="cs-lora"
          />
          <div v-if="chosenLora" class="cs-sl">
            <span class="cs-sl__lbl">Strength</span>
            <input
              v-model.number="strength"
              class="cs-range"
              type="range"
              min="0.1"
              max="1.5"
              step="0.05"
              aria-label="LoRA strength"
            />
            <span class="cs-sl__val">{{ strength.toFixed(2) }}</span>
          </div>
          <span class="cs-hint">{{ loraHint }}</span>
        </div>
        <AppInput v-model="form.top" label="Top" placeholder="a plain oatmeal crew-neck sweater" />
        <AppInput v-model="form.trousers" label="Trousers" placeholder="dark straight jeans" />
        <AppInput
          v-if="form.framing === 'full_body'"
          v-model="form.shoes"
          label="Shoes"
          placeholder="white sneakers"
        />
        <div class="cs-fld">
          <AppSelect
            v-model="form.framing"
            label="Body photos"
            :options="FRAMINGS"
            data-testid="cs-framing"
            @update:model-value="framingTouched = true"
          />
          <span class="cs-hint" data-testid="cs-framing-hint">{{ framingHint }}</span>
        </div>
        <div class="cs-fld">
          <FieldLabel>Sheets to draw</FieldLabel>
          <Segmented v-model="form.count" aria-label="Sheets to draw" :options="COUNTS" full />
          <span class="cs-hint">About a minute each.</span>
        </div>
        <div class="cs-fld">
          <FieldLabel>Sheet size</FieldLabel>
          <Segmented
            v-model="form.size"
            aria-label="Sheet size"
            :options="SIZES"
            full
            data-testid="cs-size"
          />
          <span class="cs-hint">{{ sizeHint }}</span>
        </div>
        <div class="cs-fld">
          <FieldLabel>Draw a</FieldLabel>
          <Segmented v-model="form.subject" aria-label="Draw a" :options="SUBJECTS" full />
        </div>
        <div v-if="workflowOptions.length" class="cs-fld">
          <AppSelect
            v-model="workflowId"
            label="Sheet workflow"
            :options="workflowOptions"
            compact
          />
          <p
            v-if="workflowProblem"
            class="cs-notice cs-notice--bad"
            data-testid="cs-workflow-problem"
          >
            <v-icon size="16">mdi-alert-circle-outline</v-icon>
            <span>{{ workflowProblem }}</span>
          </p>
        </div>
        <AppButton
          block
          icon-left="draw"
          :loading="working"
          :disabled="!canDraw"
          data-testid="cs-draw-button"
          @click="draw"
        >
          {{ drawLabel }}
        </AppButton>
        <span class="cs-hint"
          >A new draw goes on top. Changing the description starts a new draw
          rather than adding to this one.</span
        >
      </aside>
    </div>

    <ZoomPopout
      :open="!!zoomed"
      :src="zoomed ? sheetUrl(zoomed) : ''"
      :alt="zoomed ? `Sheet ${zoomed.n}` : ''"
      :caption="zoomed ? `Sheet ${zoomed.n} · ${sheetWords(zoomed)}` : ''"
      :has-prev="hasPrev"
      :has-next="hasNext"
      :entry="zoomEntry"
      @close="closeZoom"
      @prev="stepZoom(-1)"
      @next="stepZoom(1)"
    >
      <template v-if="zoomed?.drawnFrom" #aside>
        <div class="cs-zoomref">
          <span class="cs-secl">Drawn from</span>
          <img
            class="cs-cbig"
            :src="pictureThumbnailUrl(zoomed.drawnFrom)"
            alt="The reference picture"
          />
        </div>
      </template>
      <template #actions>
        <!-- Both choices always shown, so the control never changes size and
             says which one the sheet is on. -->
        <Segmented
          v-if="zoomed"
          :model-value="isKept(zoomed) ? 'keep' : 'drop'"
          :options="keepOptions(zoomed)"
          variant="icon-label"
          aria-label="This sheet"
          @update:model-value="setKept(zoomed, $event === 'keep')"
        />
      </template>
    </ZoomPopout>

    <FaceClustersDialog
      :open="facesOpen"
      :character="{ id: characterId, name }"
      :return-focus-to="facesOpener"
      @close="onFacesClosed"
    />

    <!-- Mounted only while open, as the Run popup's is: it loads the
         library's facets when it opens. -->
    <PicturePicker
      v-if="pickerOpen"
      open
      subtitle="for the reference"
      @close="closePicker"
      @pick="onPicked"
    />
  </div>
</template>

<script setup>
/**
 * People › character › Sheets (`/character/:id/sheets`): drawing the character
 * as character sheets, a page of the library rather than a step of a dialog.
 *
 * Draws 2-4 sheets with a MOSS-Image workflow (an ordinary
 * `POST /workflows/run`, so it queues and shows in Tasks like any run), ranks
 * them by how alike each sheet's own faces are, and cuts the ones the user
 * keeps into six pictures each on the character. Where the library has a
 * Qwen-Image-Edit sheet workflow (a card with `reference_inputs`), it can draw
 * from a reference picture plus the words instead (#2679).
 *
 * Every draw is stored on the character (`/characters/{id}/sheets/draws`):
 * its runs, its sheets as they land, the kept marks and what was cut. So
 * leaving the screen loses nothing; the runs keep going, the sidebar's Sheets
 * row and the Tasks rows lead back here, and every earlier draw stays listed
 * under the newest, still pickable. Nothing here holds state the server does
 * not also hold, except the form being typed.
 */
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useRouter } from "vue-router";
import { VIcon } from "vuetify/components";

import {
  buildSheetPrompt,
  createSheetDraw,
  deleteSheetDraw,
  listSheetDraws,
  rankSheets,
  updateSheetDraw,
  useSheets,
} from "../../api/characterSheets";
import {
  getCharacter,
  getReferencePictures,
  patchCharacter,
} from "../../api/characters";
import { listAdapters } from "../../api/modelShelf";
import { pictureThumbnailUrl } from "../../api/pictures";
import {
  getWorkflowCard,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
  sheetCopyWorkflow,
} from "../../api/workflows";
import { useSheetDrawsStore } from "../../stores/useSheetDrawsStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { API_BASE_URL, appendShareToken } from "../../utils/apiClient";
import { errorMessage } from "../../utils/apiError";
import { runKey } from "../../utils/gpuMemory";
import { buildMediaUrl } from "../../utils/media";
import TbGlobalActions from "../panels/TbGlobalActions.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
import Segmented from "../widgets/Segmented.vue";
import ZoomPopout from "../widgets/ZoomPopout.vue";
import { useZoomPopout } from "../../composables/useZoomPopout.js";
import FaceClustersDialog from "../io/FaceClustersDialog.vue";

const props = defineProps({
  /** The character whose sheets these are. */
  characterId: { type: Number, required: true },
});

const emit = defineEmits(["create-training-set"]);

// An AppSelect here: four segments do not fit the 288px inspector.
const FRAMINGS = [
  { value: "waist_up", label: "Waist-up" },
  { value: "mid_thigh", label: "Mid-thigh" },
  { value: "knees_up", label: "Knees-up" },
  { value: "full_body", label: "Full body" },
];
const COUNTS = [2, 3, 4].map((n) => ({ id: n, label: String(n) }));
// The canvas sizes `POST /character-sheets/prompt` draws at, and the passport
// photo each cuts to (a 600×768 cell of the large sheet, scaled).
const SIZES = [
  { id: "small", label: "Small", passport: "300×384" },
  { id: "medium", label: "Medium", passport: "431×552" },
  { id: "large", label: "Large", passport: "600×768" },
];
const SUBJECTS = [
  { id: "woman", label: "Woman" },
  { id: "man", label: "Man" },
];
const MOSS = "moss_image";
// What draws a sheet from a reference picture (#2679).
// ponytail: one engine, the owner's pick; no probe has measured it yet.
const QWEN = "qwen";
const MODES = [
  { id: "text", label: "Text" },
  { id: "reference", label: "Reference and text" },
];
const ORDINALS = ["first", "second", "third", "fourth"];
const POLL_MS = 2500;
// Panels are filed before their faces are read; a training set ranks by face,
// so the cut waits for them, but not forever: a slow queue still hands over.
const FACE_WAIT_MS = 90_000;
// A sheet whose faces are still unread this long after its runs ended (no face
// model, a failed extraction, a deleted picture) stops being waited for: it
// can be chosen unscored, and the polling stops.
const SHEET_FACE_WAIT_MS = 120_000;
const HEIGHT_WORDS = /\b(tall|short|petite|height|legs?|leggy|long-legged)\b/i;

const router = useRouter();
const tasks = useTasksStore();
const sheetDraws = useSheetDrawsStore();
const character = ref(null);
const name = computed(() => character.value?.name || "");
const form = reactive({
  description: "",
  body: "",
  top: "",
  trousers: "",
  shoes: "",
  framing: "mid_thigh",
  size: "large",
  count: 4,
  subject: "woman",
});
const framingTouched = ref(false);
const savedDescription = ref("");
const loras = ref([]);
const loraSha = ref("");
const strength = ref(0.8);
const workflows = ref([]);
// Cards that read a picture while drawing on a canvas of their own, and the
// edit card one could be made from when there is none.
const referenceCards = ref([]);
const convertible = ref(null);
const mode = ref("text"); // text | reference
// The picture the next sheets are drawn from.
const referenceId = ref(null);
const pickerOpen = ref(false);
const pickButton = ref(null);
// The chosen workflow's picture inputs, as the pre-flight names them.
const workflowInputs = ref([]);
const workflowsLoaded = ref(false);
const workflowId = ref("");
// The chosen workflow's pre-flight: its width/height inputs, and why it can't
// draw a sheet ("" while it can, null while still checking).
const workflowSizes = ref([]);
const workflowProblem = ref(null);
const referenceIds = ref([]);
// The draws, newest first, as the server has them.
const draws = ref([]);
// picture id -> RankedSheet, for every draw's sheets.
const ranking = ref({});
// draw id -> how many faces its last ranking measured closeness against.
const rankedAgainst = ref({});
// draw id -> true once its sheets' faces stopped being waited for.
const gaveUp = ref({});
// draw id -> when its runs were first seen all ended.
const endedAt = {};
// Draws whose kept marks were changed here: none is kept for the user after.
const touched = new Set();
const mainPane = ref(null);
const working = ref(false);
const filing = ref(false);
// What the last cut filed: { count }.
const filed = ref(null);
const error = ref("");
// Drawing waits for the character's stored description, so a typed one never
// overwrites it.
const loaded = ref(false);
let timer = null;
let ticking = false;
let alive = true;

const fromReference = computed(() => mode.value === "reference");
const engine = computed(() =>
  fromReference.value
    ? { family: QWEN, name: "Qwen-Image" }
    : { family: MOSS, name: "MOSS-Image" },
);

const chosenLora = computed(
  () => loras.value.find((lora) => lora.sha256 === loraSha.value) || null,
);
const trigger = computed(() => {
  const words = chosenLora.value?.trigger_words;
  return (Array.isArray(words) ? words[0] : words) || "";
});
const loraName = computed(() =>
  chosenLora.value
    ? chosenLora.value.display_name || chosenLora.value.filename
    : "",
);

const loraOptions = computed(() => [
  { value: "", label: "No LoRA" },
  ...loras.value.map((lora) => {
    const fits = lora.base_model_family === engine.value.family;
    return {
      value: lora.sha256,
      label: lora.display_name || lora.filename,
      disabled: !fits,
      chips: [
        fits
          ? lora.base_model || engine.value.name
          : `For ${lora.base_model || "another model"}; sheets run ${engine.value.name}`,
      ],
    };
  }),
]);

const loraHint = computed(() => {
  const only = `Only LoRAs for ${engine.value.name}, the sheet workflow's model, can be chosen.`;
  if (chosenLora.value && trigger.value) {
    return `Loaded into the sheet workflow, with the trigger word ${trigger.value} added to every sheet prompt. ${only}`;
  }
  if (chosenLora.value) return `Loaded into the sheet workflow. ${only}`;
  if (fromReference.value) {
    return "Without a LoRA, the reference picture alone carries the face.";
  }
  return "Without a LoRA, each sheet is a slightly different person, so you pick one.";
});

const modeCards = computed(() =>
  fromReference.value ? referenceCards.value : workflows.value,
);

const workflowOptions = computed(() =>
  modeCards.value.map((card) => ({ value: card.id, label: card.name })),
);

/** The newest LoRA that loads into the mode's sheet workflow. */
function fittingLora() {
  return (
    loras.value.find((l) => l.base_model_family === engine.value.family)
      ?.sha256 || ""
  );
}

// Each mode has its own workflows and its own model, so its own LoRAs.
watch(mode, () => {
  workflowId.value = modeCards.value[0]?.id || "";
  if (chosenLora.value?.base_model_family !== engine.value.family) {
    loraSha.value = fittingLora();
  }
});

const referenceHint = computed(() => {
  const faces = referenceIds.value.length
    ? `${name.value}'s reference faces. `
    : "";
  return `${faces}The sheet workflow takes one picture; the dashed tile opens the picker.`;
});

/** The picker opens programmatically, so Vuetify has no activator to hand
 * focus back to: the pick button takes it. */
function closePicker() {
  pickerOpen.value = false;
  nextTick(() => pickButton.value?.focus());
}

function onPicked(picture) {
  closePicker();
  if (picture?.id) referenceId.value = picture.id;
}

const suggestedFraming = computed(() =>
  chosenLora.value ? "full_body" : "mid_thigh",
);

watch(suggestedFraming, (framing) => {
  if (!framingTouched.value) form.framing = framing;
});

const framingHint = computed(() => {
  const reason = chosenLora.value
    ? "Suggested: full body. The LoRA holds the face, so the photos can show the whole body."
    : "Suggested: mid-thigh, the framing that held its layout best. With no LoRA the sheet alone carries the face; waist-up gives the faces the most pixels.";
  const height =
    form.framing !== "full_body" && HEIGHT_WORDS.test(form.body)
      ? " This framing won't show height."
      : "";
  return reason + height;
});

const sizeHint = computed(() => {
  const size = SIZES.find((s) => s.id === form.size);
  return `Passport photos come out ${size.passport}. Larger is slower to draw.`;
});

const descriptionHint = computed(() => {
  if (fromReference.value) {
    const from = savedDescription.value ? `From ${name.value}. ` : "";
    return `${from}In this mode the picture sets the face; these words are sent with it.`;
  }
  if (savedDescription.value) {
    return `From ${name.value}. Changes here apply to these sheets only.`;
  }
  return `${name.value} has no description yet. This one is saved as theirs when the first sheets are drawn.`;
});

const likenessNotice = computed(() => {
  if (chosenLora.value) {
    return `Drawn with ${loraName.value}, a LoRA of ${name.value}, so the sheets start from that likeness. Without a LoRA they would be a new likeness drawn from words.`;
  }
  const count = Number(character.value?.image_count) || 0;
  if (count > 0) {
    return `These sheets are drawn from words, not from ${name.value}'s ${count} pictures. What they make is a new likeness, filed on ${name.value} and ranked beside the pictures already there.`;
  }
  return "";
});

/**
 * What the next draw is drawn with. Drawing again with the same is "Draw N
 * more", added to the newest draw; anything else starts a new draw on top,
 * since its sheets are of another prompt, not more of this one.
 */
const signature = computed(() =>
  JSON.stringify([
    form.description.trim(),
    form.body,
    form.top,
    form.trousers,
    form.framing === "full_body" ? form.shoes : "",
    form.framing,
    form.size,
    form.subject,
    workflowId.value,
    loraSha.value,
    chosenLora.value ? strength.value : 0,
    fromReference.value ? referenceId.value : null,
  ]),
);

const addsToNewest = computed(
  () => !!draws.value[0] && draws.value[0].settings?.signature === signature.value,
);

const drawLabel = computed(() => {
  const n = form.count;
  return addsToNewest.value
    ? `Draw ${n} more`
    : `Draw ${n} ${n === 1 ? "sheet" : "sheets"}`;
});

function runEnded(run) {
  return ["completed", "failed"].includes(run.status);
}

function score(sheet) {
  return (sheet.agreement ?? -1) + (sheet.closeness ?? 0);
}

/** Drawn from a picture, closeness to it is what matters most. */
function better(drawnFrom, a, b) {
  if (!drawnFrom) return score(b) - score(a);
  return (
    (b.closeness ?? -1) - (a.closeness ?? -1) ||
    (b.agreement ?? -1) - (a.agreement ?? -1)
  );
}

const liveRuns = computed(
  () => new Map(tasks.comfyuiRuns.map((run) => [runKey(run), run])),
);

/** "Today 14:02", "Yesterday 09:10" or the date. */
function whenWords(iso) {
  if (!iso) return "";
  const normal = /([zZ]|[+-]\d{2}:\d{2})$/.test(iso) ? iso : `${iso}Z`;
  const at = new Date(normal);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(new Date()) - day(at)) / 86_400_000);
  if (days === 0) return `Today ${time}`;
  if (days === 1) return `Yesterday ${time}`;
  return at.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

/** One draw as the screen shows it: its places, its sheets and its state. */
function buildView(draw) {
  const drawnFrom = draw.drawn_from ?? null;
  const live = liveRuns.value;
  const scored = new Map();
  for (const run of draw.runs) {
    for (const id of run.picture_ids || []) {
      scored.set(id, {
        picture_id: id,
        faces_read: false,
        ...(ranking.value[id] || {}),
        drawId: draw.id,
        drawnFrom,
      });
    }
  }
  // A sheet lands in its run's place and nothing moves as the others arrive;
  // its number is its place.
  const places = [];
  let drawingPercent = 0;
  for (const run of draw.runs) {
    const drawn = (run.picture_ids || []).filter((id) => scored.has(id));
    for (const id of drawn) places.push({ key: `sheet-${id}`, sheet: scored.get(id) });
    if (drawn.length) continue;
    const progress = live.get(run.key);
    const drawing =
      !runEnded(run) && (progress?.status ?? run.status) === "running";
    const percent = drawing ? Math.round(Number(progress?.percent) || 0) : null;
    if (drawing) drawingPercent += percent;
    places.push({
      key: run.key,
      label: runEnded(run) ? "didn't draw" : drawing ? "drawing" : "queued",
      percent,
    });
  }
  const slots = places.map((slot, index) => {
    if (slot.sheet) slot.sheet.n = index + 1;
    return { ...slot, n: index + 1 };
  });
  const sheets = [...scored.values()];
  const ended = sheets.filter((s) => s.faces_read);
  const holding = sheets
    .filter((s) => s.faces_read && !s.broken && s.agreement_words !== "faces don't hold")
    .sort((a, b) => better(drawnFrom, a, b));
  const runsDone = draw.runs.every(runEnded);
  const allEnded =
    runsDone && (gaveUp.value[draw.id] || sheets.every((s) => s.faces_read));
  const failures = draw.runs
    .filter((run) => run.status === "failed")
    .map((run) => run.message || "the run failed.");
  const total = draw.runs.length;
  const endedRuns = draw.runs.filter(runEnded).length;
  const waiting = total - endedRuns;
  let noneClose = "";
  if (drawnFrom && allEnded && holding.length && !holding.some((s) => s.close)) {
    noneClose = !rankedAgainst.value[draw.id]
      ? "No face was read in the reference picture, so the sheets can't be compared with it and none is kept for you. A reference with the face to the camera works best."
      : `None of the ${ended.length} sheets came close to the reference, so none is kept for you. Drawing more usually finds one. A different reference picture, face to the camera, helps too.`;
  }
  const when = whenWords(draw.created_at);
  let state;
  if (waiting) {
    const running = slots.some((slot) => slot.label === "drawing");
    state = running ? `drawing ${endedRuns + 1} of ${total}` : `${waiting} queued`;
  } else {
    const kept = draw.kept.length;
    const keptWords = draw.used_sheets?.length
      ? `${draw.used_sheets.length} used`
      : kept
        ? `${kept} kept`
        : "none kept";
    state = `${sheets.length} ${sheets.length === 1 ? "sheet" : "sheets"} · ${keptWords}`;
  }
  return {
    draw,
    when,
    heading: when ? `${when} · ${state}` : state,
    percent: waiting
      ? Math.round((endedRuns * 100 + drawingPercent) / total)
      : null,
    slots,
    sheets,
    ended,
    holding,
    allEnded,
    failures,
    noneHold: allEnded && ended.length > 0 && !holding.length,
    noneClose,
    best: drawnFrom ? holding.find((s) => s.close) : holding[0],
  };
}

const views = computed(() => draws.value.map(buildView));

const sheetCount = computed(() =>
  views.value.reduce((sum, v) => sum + v.sheets.length, 0),
);

const subLine = computed(() => {
  const n = draws.value.length;
  if (!n) return name.value;
  const m = sheetCount.value;
  return `${name.value} · ${n} ${n === 1 ? "draw" : "draws"} · ${m} ${m === 1 ? "sheet" : "sheets"}`;
});

function drawOf(sheet) {
  return draws.value.find((d) => d.id === sheet.drawId) || null;
}

function isKept(sheet) {
  return !!drawOf(sheet)?.kept.includes(sheet.picture_id);
}

function isUsed(sheet) {
  return !!drawOf(sheet)?.used_sheets?.includes(sheet.picture_id);
}

/** Kept and not cut yet: what Use cuts, draw by draw. */
const toUse = computed(() =>
  views.value.flatMap((v) =>
    v.sheets.filter(
      (s) => v.draw.kept.includes(s.picture_id) && !isUsed(s),
    ),
  ),
);

const canUse = computed(
  () =>
    toUse.value.length > 0 &&
    toUse.value.every((s) => s.faces_read || gaveUp.value[s.drawId]) &&
    !working.value &&
    !filing.value,
);

const useLabel = computed(() => {
  const keep = toUse.value;
  if (keep.length === 0) return "Use a sheet";
  if (keep.length === 1) return `Use sheet ${keep[0].n}`;
  return `Use ${keep.length} sheets`;
});

/** Sheets to use whose faces look like different people. Without a LoRA each
 * sheet is its own person, so keeping two usually mixes faces; with one,
 * several sheets can be the same person and pass. Warned, not refused: the
 * owner may want two looks of one character. */
const mixedWarning = computed(() => {
  const keep = toUse.value;
  const pairs = [];
  for (const sheet of keep) {
    for (const id of sheet.different_from || []) {
      const other = keep.find((k) => k.picture_id === id);
      if (other && sheet.n < other.n) pairs.push([sheet.n, other.n]);
    }
  }
  if (!pairs.length) return "";
  // A sheet's number is its place in its own draw: named only when every kept
  // sheet is in one draw, so the numbers say which sheets they are.
  if (pairs.length === 1 && new Set(keep.map((s) => s.drawId)).size === 1) {
    return `Sheets ${pairs[0][0]} and ${pairs[0][1]} look like different people; using both mixes faces.`;
  }
  return "Kept sheets look like different people; using them mixes faces.";
});

function pickIntro(view) {
  if (view.draw.drawn_from) {
    return `Each sheet says how close it comes to the picture it was drawn from. The closest is kept for you if it comes close enough. Keep the ones to use: the six photos of each become pictures of ${name.value}. Every sheet stays in the Scrapheap, used or not.`;
  }
  const base = `Each sheet says how well its own faces agree. Keep the ones to use: the six photos of each become pictures of ${name.value}. Every sheet stays in the Scrapheap, used or not, where you can restore any you like.`;
  if (!referenceIds.value.length) return base;
  return `${base} Each is also compared with ${name.value}'s reference faces: a sheet that agrees with itself but drifts from them is a different person drawn well.`;
}

function sheetWords(sheet) {
  if (!sheet.faces_read) {
    return gaveUp.value[sheet.drawId] ? "faces not read" : "reading faces…";
  }
  if (sheet.broken) return "more than one person in a photo";
  const words = [sheet.agreement_words];
  if (sheet.close != null) {
    const near = closeWords(sheet);
    // Drawn from a picture, closeness leads, with its number.
    if (sheet.drawnFrom) words.unshift(`${sheet.closeness.toFixed(2)} ${near}`);
    else words.push(near);
  }
  if (isUsed(sheet)) words.push("used");
  else if (isKept(sheet)) words.push("kept");
  return words.join(" · ");
}

/** Closeness in words: to the picture drawn from, else to the character. */
function closeWords(sheet) {
  const whom = sheet.drawnFrom ? "the reference" : name.value;
  return sheet.close ? `close to ${whom}` : `drifts from ${whom}`;
}

function meterWidth(sheet) {
  return `${Math.round(Math.min(1, Math.max(0, sheet.closeness)) * 100)}%`;
}

function sheetLocked(sheet) {
  return (
    (!sheet.faces_read && !gaveUp.value[sheet.drawId]) ||
    isUsed(sheet) ||
    filing.value
  );
}

function keepOptions(sheet) {
  const disabled = sheetLocked(sheet);
  return [
    { id: "keep", label: "Keep", icon: "check", disabled },
    { id: "drop", label: "Drop", icon: "delete-outline", disabled },
  ];
}

/** Put a draw the server answered with in its place. */
function store(saved) {
  const at = draws.value.findIndex((d) => d.id === saved.id);
  if (at >= 0) draws.value.splice(at, 1, saved);
  else draws.value.unshift(saved);
}

// draw id -> the last kept save queued for it, and how many were queued.
const keptSaves = {};

/** Saves for one draw go one after another, so the server ends on the last
 * tick; only the newest answer is shown, and a failure reads the draws back. */
function saveKept(draw, kept) {
  store({ ...draw, kept });
  const queue = (keptSaves[draw.id] ||= { last: Promise.resolve(), n: 0 });
  const n = ++queue.n;
  queue.last = queue.last.then(async () => {
    try {
      const saved = await updateSheetDraw(props.characterId, draw.id, { kept });
      if (n === queue.n) store(saved);
      sheetDraws.refresh();
    } catch (e) {
      console.warn("Couldn't store the kept sheets", draw.id, kept, e);
      error.value = `Couldn't keep the sheet. ${errorMessage(e, "Please try again.")}`;
      try {
        await reload();
      } catch (again) {
        console.warn("Couldn't read the draws back", draw.id, again);
      }
    }
  });
  return queue.last;
}

function toggleKeep(sheet) {
  const draw = drawOf(sheet);
  if (!draw || sheetLocked(sheet)) return;
  touched.add(draw.id);
  const id = sheet.picture_id;
  saveKept(
    draw,
    draw.kept.includes(id) ? draw.kept.filter((k) => k !== id) : [...draw.kept, id],
  );
}

function setKept(sheet, on) {
  if (isKept(sheet) !== on) toggleKeep(sheet);
}

const zoomList = computed(() =>
  views.value.flatMap((v) =>
    v.slots.filter((slot) => slot.sheet).map((slot) => slot.sheet),
  ),
);
// The sheet in the zoom pop-out, and the wheel that opened it, if one did.
// Closing goes back to the sheets, focus on the sheet's own zoom control.
const {
  zoomedId,
  zoomEntry,
  hasPrev,
  hasNext,
  openZoom,
  onTileWheel,
  stepZoom,
  closeZoom,
} = useZoomPopout(
  computed(() => zoomList.value.map((s) => s.picture_id)),
  (id) =>
    mainPane.value
      ?.querySelector(`[data-testid="cs-zoom-open"][data-sheet="${id}"]`)
      ?.focus(),
);
const zoomed = computed(
  () => zoomList.value.find((s) => s.picture_id === zoomedId.value) || null,
);

/** The sheet's own file, full size; the thumbnail until its format is known. */
function sheetUrl(sheet) {
  if (!sheet.format) return pictureThumbnailUrl(sheet.picture_id);
  return appendShareToken(
    buildMediaUrl({ backendUrl: API_BASE_URL, image: { id: sheet.picture_id, format: sheet.format } }),
  );
}

// Pre-flight: a sheet is drawn at a size of its own, so the workflow must have
// one to set, and it must be able to run at all.
watch(workflowId, async (id) => {
  workflowSizes.value = [];
  workflowInputs.value = [];
  workflowProblem.value = id ? null : "";
  if (!id) return;
  const reference = fromReference.value;
  try {
    const [{ card }, check] = await Promise.all([
      getWorkflowCard(id),
      preflightWorkflowRun({ workflow_id: id, count: 1 }, { cached: true }),
    ]);
    if (id !== workflowId.value) return;
    const cardName = card?.name || "This workflow";
    const sizes = (card?.defaults || []).filter((d) =>
      ["width", "height"].includes(d.input_name),
    );
    const groups = check?.groups || [];
    // The draw fills the picture inputs itself; a pinned one keeps its pin.
    const inputs = reference
      ? groups
          .flatMap((g) => g.picture_inputs || [])
          .filter((i) => i.mode !== "fixed")
      : [];
    const refused = groups.some((g) =>
      (g.reasons || []).some(
        (r) => !(reference && r.code === "picture_input_unfilled"),
      ),
    );
    workflowSizes.value = sizes;
    workflowInputs.value = inputs;
    workflowProblem.value = !sizes.length
      ? `${cardName} has no picture size to set, so it can't draw a sheet.`
      : refused
        ? `${cardName} can't run as it is. Open it from Workflows to see why.`
        : reference && !inputs.length
          ? `${cardName} has no picture input to put the reference in.`
          : reference && !groups.some((g) => g.prompt?.positive_settable)
            ? `${cardName} has no prompt PixlStash can write the sheet into.`
            : "";
  } catch (e) {
    if (id !== workflowId.value) return;
    console.warn("Couldn't check the sheet workflow", id, e);
    workflowProblem.value = `Couldn't check this workflow: ${errorMessage(e)}`;
  }
});

function stopPolling() {
  if (timer) clearTimeout(timer);
  timer = null;
}

function schedule() {
  // A tick in flight schedules the next one itself when it ends.
  if (ticking || timer || !alive) return;
  timer = setTimeout(tick, POLL_MS);
}

/** Read the draws again; the server reads every run's ending into them. */
async function reload() {
  const { draws: list } = await listSheetDraws(props.characterId);
  if (alive) draws.value = list || [];
}

/** Rank every draw with a sheet whose faces are not read yet, each against
 * what it was drawn from. */
async function rankPending() {
  for (const draw of draws.value) {
    const ids = draw.runs.flatMap((run) => run.picture_ids || []);
    if (!ids.length || ids.every((id) => ranking.value[id]?.faces_read)) continue;
    let answer;
    try {
      answer = draw.drawn_from
        ? await rankSheets(props.characterId, ids, [draw.drawn_from])
        : await rankSheets(props.characterId, ids);
    } catch (e) {
      // One draw that can't be ranked must not hold up the others.
      console.warn("Couldn't rank the sheets of a draw", draw.id, ids, e);
      continue;
    }
    if (!alive) return;
    rankedAgainst.value = { ...rankedAgainst.value, [draw.id]: answer.references || 0 };
    ranking.value = {
      ...ranking.value,
      ...Object.fromEntries(answer.sheets.map((sheet) => [sheet.picture_id, sheet])),
    };
  }
}

/** A draw that has ended is seen; the best of it is kept for the user unless
 * they kept or dropped one themselves. */
async function settle() {
  const now = Date.now();
  for (const view of views.value) {
    const id = view.draw.id;
    if (!view.draw.runs.every(runEnded)) {
      delete endedAt[id];
      continue;
    }
    endedAt[id] ??= now;
    if (!gaveUp.value[id] && now - endedAt[id] > SHEET_FACE_WAIT_MS) {
      gaveUp.value = { ...gaveUp.value, [id]: true };
    }
  }
  for (const view of views.value) {
    if (!view.allEnded || view.draw.seen) continue;
    const patch = { seen: true };
    if (!touched.has(view.draw.id) && !view.draw.kept.length && view.best) {
      patch.kept = [view.best.picture_id];
    }
    try {
      store(await updateSheetDraw(props.characterId, view.draw.id, patch));
      sheetDraws.refresh();
    } catch (e) {
      console.warn("Couldn't mark the sheet draw seen", view.draw.id, e);
    }
  }
}

async function tick() {
  timer = null;
  ticking = true;
  try {
    await reload();
    await rankPending();
  } catch (e) {
    console.warn("Couldn't read the character's sheet draws", props.characterId, e);
  } finally {
    ticking = false;
  }
  if (!alive) return;
  await settle();
  if (alive && views.value.some((v) => !v.allEnded)) schedule();
}

async function draw() {
  if (working.value || !canDraw.value) return;
  working.value = true;
  error.value = "";
  // What the draw runs with, read before any await: a choice changed while
  // the prompt is built must not reach this run (the sizes are the checked
  // workflow's own).
  const workflow = workflowId.value;
  const sizes = workflowSizes.value;
  const lora = chosenLora.value;
  const weight = strength.value;
  const count = form.count;
  const reference = fromReference.value ? referenceId.value : null;
  const inputs = workflowInputs.value;
  const drawnWith = signature.value;
  const adding = addsToNewest.value ? draws.value[0] : null;
  const settings = {
    signature: drawnWith,
    ...form,
    mode: mode.value,
    workflow_id: workflow,
    lora_sha: lora?.sha256 || "",
    strength: weight,
    reference_id: reference,
  };
  const description = form.description.trim();
  let keys;
  try {
    if (!savedDescription.value && description) {
      await patchCharacter(props.characterId, { description });
      savedDescription.value = description;
    }
    const sheet = await buildSheetPrompt({
      description,
      body: form.body,
      top: form.top,
      trousers: form.trousers,
      shoes: form.shoes,
      framing: form.framing,
      size: form.size,
      subject: form.subject,
      trigger: trigger.value,
      // An edit model takes no layout: the sheet in one sentence, naming
      // the person in the picture.
      ...(reference ? { reference: true, layout: "prose" } : {}),
    });
    const answer = await runWorkflowCard({
      workflow_id: workflow,
      prompt: sheet.prompt,
      negative: sheet.negative,
      values: sizes.map((d) => ({
        slot_label: d.slot_label,
        input_name: d.input_name,
        value: d.input_name === "width" ? sheet.width : sheet.height,
      })),
      add_loras: lora
        ? [
            {
              sha256: lora.sha256,
              strength_model: weight,
              strength_clip: weight,
            },
          ]
        : [],
      ...(reference
        ? {
            inputs: inputs.map((input) => ({
              slot_label: input.slot_label,
              input_name: input.input_name,
              picture_id: reference,
            })),
          }
        : {}),
      count,
      // A sheet is six photos of one person: none of its faces is anybody's
      // until it is chosen and cut, so nothing here names the character.
      character_ids: [],
      // Not the library's until kept: imported straight to the Scrapheap.
      hold: true,
      label: `Character sheet · ${name.value}`,
    });
    keys = (answer?.prompts || []).map(runKey).filter(Boolean);
    if (!keys.length) {
      throw new Error(
        answer?.error ||
          "The workflow can't run as it is. Open it from Workflows to see why.",
      );
    }
  } catch (e) {
    error.value = `Couldn't start the sheets. ${errorMessage(e, "Please try again.")}`;
    working.value = false;
    return;
  }
  try {
    const saved = adding
      ? await updateSheetDraw(props.characterId, adding.id, { add_run_keys: keys })
      : await createSheetDraw(props.characterId, {
          settings,
          drawn_from: reference,
          run_keys: keys,
        });
    store(saved);
    delete endedAt[saved.id];
    gaveUp.value = { ...gaveUp.value, [saved.id]: false };
    sheetDraws.refresh();
    schedule();
  } catch (e) {
    console.error("Sheet runs started but their draw couldn't be stored", keys, e);
    error.value = `The sheets are drawing, but couldn't be listed here. They land in the Scrapheap, and Tasks shows them. ${errorMessage(e, "")}`;
  } finally {
    working.value = false;
  }
}

async function removeDraw(target) {
  if (working.value || filing.value) return;
  try {
    await deleteSheetDraw(props.characterId, target.id);
    draws.value = draws.value.filter((d) => d.id !== target.id);
    sheetDraws.refresh();
  } catch (e) {
    console.warn("Couldn't delete the sheet draw", target.id, e);
    error.value = `Couldn't delete the draw. ${errorMessage(e, "Please try again.")}`;
  }
}

/** Store a copy of the library's Qwen edit workflow that draws at the sheet's
 * size, and offer the mode it makes possible. */
async function makeSheetWorkflow() {
  if (working.value || !convertible.value) return;
  working.value = true;
  error.value = "";
  try {
    await sheetCopyWorkflow(convertible.value.id);
    const { cards } = await listWorkflowCards();
    if (!alive) return;
    sortCards(cards);
    // With no text workflow the new mode is the only one: go to it, as load() does.
    if (!modeCards.value.length && referenceCards.value.length) {
      mode.value = "reference";
    }
  } catch (e) {
    error.value = `Couldn't make the sheet workflow. ${errorMessage(e, "Please try again.")}`;
  } finally {
    working.value = false;
  }
}

async function use() {
  if (!canUse.value) return;
  const groups = new Map();
  for (const sheet of toUse.value) {
    groups.set(sheet.drawId, [...(groups.get(sheet.drawId) || []), sheet.picture_id]);
  }
  filing.value = true;
  error.value = "";
  filed.value = null;
  const ids = [];
  try {
    for (const [drawId, keep] of groups) {
      const { picture_ids: made } = await useSheets(props.characterId, keep, drawId);
      ids.push(...made);
    }
  } catch (e) {
    error.value = `Couldn't use the sheets. ${errorMessage(e, "Please try again.")}`;
  }
  // Only the panels' faces may still be unread. A failure from here on is no
  // reason to offer the cut again.
  const deadline = Date.now() + FACE_WAIT_MS;
  while (ids.length && alive && Date.now() < deadline) {
    try {
      const answer = await rankSheets(props.characterId, ids);
      if (answer.sheets.every((sheet) => sheet.faces_read)) break;
    } catch (e) {
      console.warn("Couldn't read the new pictures' faces", ids, e);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  if (!alive) return;
  try {
    await reload();
  } catch (e) {
    console.warn("Couldn't read the sheet draws after the cut", props.characterId, e);
  }
  sheetDraws.refresh();
  if (ids.length) filed.value = { count: ids.length };
  filing.value = false;
}

const canDraw = computed(
  () =>
    loaded.value &&
    !!workflowId.value &&
    workflowProblem.value === "" &&
    // The picture says who; in words alone the description has to.
    (fromReference.value ? !!referenceId.value : !!form.description.trim()) &&
    !filing.value,
);

/** The cards each mode can draw with, most recently used first. */
function sortCards(cards) {
  const recent = (a, b) =>
    String(b.last_used || "").localeCompare(String(a.last_used || ""));
  const of = (card, family) =>
    (card.models || []).some((m) => m.base_model_family === family);
  workflows.value = cards
    .filter((card) => card.type === "txt2img" && of(card, MOSS))
    .sort(recent);
  referenceCards.value = cards
    .filter((card) => card.reference_inputs > 0 && of(card, QWEN))
    .sort(recent);
  // An edit workflow that draws on its picture's own canvas: a sheet copy of
  // it is what the reference mode runs.
  convertible.value = referenceCards.value.length
    ? null
    : cards
        .filter((card) => card.type === "img2img" && of(card, QWEN))
        .sort(recent)[0] || null;
}

/** The form as the newest draw was drawn with, so coming back to the screen
 * comes back to the same draw. A workflow or LoRA gone since is not restored. */
async function restoreForm(settings) {
  if (!settings) return;
  for (const key of Object.keys(form)) {
    if (settings[key] != null) form[key] = settings[key];
  }
  framingTouched.value = true;
  if (settings.mode === "reference" && referenceCards.value.length) {
    mode.value = "reference";
  } else if (settings.mode === "text" && workflows.value.length) {
    mode.value = "text";
  }
  // The mode's own watcher picks its first workflow and a fitting LoRA first.
  await nextTick();
  if (modeCards.value.some((card) => card.id === settings.workflow_id)) {
    workflowId.value = settings.workflow_id;
  }
  if (settings.lora_sha === "" || loras.value.some((l) => l.sha256 === settings.lora_sha)) {
    loraSha.value = settings.lora_sha;
  }
  if (typeof settings.strength === "number") strength.value = settings.strength;
  if (settings.reference_id != null) referenceId.value = settings.reference_id;
}

async function load() {
  const id = props.characterId;
  const [found, adapters, cards, references, stored] = await Promise.allSettled([
    getCharacter(id),
    listAdapters({ characterId: id }),
    listWorkflowCards(),
    getReferencePictures(id),
    listSheetDraws(id),
  ]);
  if (!alive) return;
  if (found.status === "fulfilled") {
    character.value = found.value;
    savedDescription.value = (found.value?.description || "").trim();
    if (!form.description) form.description = savedDescription.value;
  }
  if (adapters.status === "fulfilled") {
    loras.value = [...adapters.value].sort((a, b) =>
      String(b.added_at || "").localeCompare(String(a.added_at || "")),
    );
    // The newest LoRA that loads into the sheet workflow is preselected.
    loraSha.value = fittingLora();
  }
  if (references.status === "fulfilled") showReferences(references.value);
  if (cards.status === "fulfilled") {
    sortCards(cards.value.cards);
    // A character with a face is drawn from it where a workflow can; one
    // with no pictures, from words.
    if (
      referenceCards.value.length &&
      (referenceIds.value.length || !workflows.value.length)
    ) {
      mode.value = "reference";
    }
    workflowId.value = modeCards.value[0]?.id || "";
  }
  if (stored.status === "fulfilled") {
    draws.value = stored.value?.draws || [];
    await nextTick();
    await restoreForm(draws.value[0]?.settings);
  }
  workflowsLoaded.value = true;
  loaded.value = found.status === "fulfilled";
  if (!loaded.value) {
    error.value = "Couldn't read this character. Reload the page to try again.";
  } else if (stored.status === "rejected") {
    error.value = `Couldn't read the earlier draws. ${errorMessage(stored.reason, "Reload the page to try again.")}`;
  }
  for (const result of [found, adapters, cards, references, stored]) {
    if (result.status === "rejected") {
      console.warn("Couldn't load part of the Sheets screen", id, result.reason);
    }
  }
  if (alive && draws.value.length) tick();
}

function goBack() {
  if (window.history.state?.back) router.back();
  else router.push({ name: "character", params: { id: String(props.characterId) } });
}

function showReferences(body) {
  referenceIds.value = (body?.reference_picture_ids || []).slice(0, 4);
  referenceId.value = referenceIds.value[0] ?? null;
}

// The faces dialog: the reference cluster steers the reference faces, so they
// are read again when it closes.
const facesOpen = ref(false);
const facesOpener = ref(null);

function openFaces(event) {
  facesOpener.value = event?.currentTarget ?? null;
  facesOpen.value = true;
}

async function onFacesClosed() {
  facesOpen.value = false;
  try {
    showReferences(await getReferencePictures(props.characterId));
  } catch (e) {
    console.warn("Couldn't reload the reference faces", props.characterId, e);
  }
}

onMounted(load);
onBeforeUnmount(() => {
  alive = false;
  stopPolling();
});
</script>

<style scoped>
.csv {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
}

.csv-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 36px;
  box-sizing: border-box;
  padding: 0 var(--space-3) 0 var(--space-3);
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  flex-shrink: 0;
}

.csv-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}

.csv-sub {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-toolbar-text), var(--opacity-text-secondary));
}

.csv-tb-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.csv-body {
  flex: 1;
  min-height: 0;
  display: flex;
}

.csv-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-4) var(--space-6) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.csv-inspector {
  flex: 0 0 var(--stats-panel-w);
  width: var(--stats-panel-w);
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  background: rgb(var(--v-theme-surface));
  border-left: 1px solid rgb(var(--v-theme-divider));
}

.cs-empty {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 60ch;
}

.cs-empty h3 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.cs-empty span,
.cs-intro,
.cs-hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-hint {
  font-size: var(--text-2xs);
}

.cs-draw {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cs-dhead {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h);
}

/* The draw's progress: the Tasks panel's run line, beside the heading. */
.cs-dline {
  flex: 0 0 120px;
  height: 2px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.cs-dline span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-primary));
  transition: width var(--dur-3) var(--ease-standard);
}

.cs-dpct {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-ddel {
  margin-left: auto;
}

.cs-fld {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cs-sl {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.cs-sl__lbl {
  min-width: 56px;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-range {
  flex: 1;
  min-width: 0;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.cs-sl__val {
  min-width: 4ch;
  text-align: right;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.cs-notice {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-input-background));
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.cs-notice .v-icon {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-notice--warn .v-icon {
  color: rgb(var(--v-theme-surface-warning));
}

.cs-notice--bad .v-icon {
  color: rgb(var(--v-theme-surface-error));
}

.cs-notice--ok .v-icon {
  color: rgb(var(--v-theme-surface-success));
}

.cs-sheets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-3);
}

/* Earlier draws: smaller and dimmed, still pickable. */
.cs-sheets--past {
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
}

.cs-draw--past .cs-tile {
  opacity: var(--opacity-text-secondary);
}

.cs-draw--past .cs-tile:hover,
.cs-draw--past .cs-tile:focus-within {
  opacity: 1;
}

.cs-sheets li {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cs-sheetimg {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 5 / 3;
  padding: 0;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  cursor: pointer;
}

.cs-sheetimg:disabled {
  cursor: default;
}

.cs-sheetimg img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.cs-sheetimg--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}

.cs-sheetimg--wait {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  border: 1px dashed rgb(var(--v-theme-border));
  background: transparent;
  cursor: default;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The run's percent, as the Tasks panel's run line draws it. */
.cs-progress {
  width: 50%;
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.cs-progress span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-primary));
  transition: width var(--dur-3) var(--ease-standard);
}

.cs-sheetimg__rk,
.cs-sheetimg__ck {
  position: absolute;
  top: var(--space-2);
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.cs-sheetimg__rk {
  left: var(--space-2);
  min-width: 18px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}

.cs-sheetimg__ck {
  right: var(--space-2);
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.cs-cap {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-cap b,
.cs-item b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

/* The reference picture, large: what the sheets are judged against. */
.cs-cbig {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 5;
  object-fit: cover;
  border-radius: var(--radius-md);
}

.cs-cstrip {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--space-2);
}

.cs-reft {
  position: relative;
  aspect-ratio: 1 / 1;
  padding: 0;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  cursor: pointer;
}

.cs-reft img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cs-reft--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}

.cs-reft--add {
  display: grid;
  place-items: center;
  border: 1px dashed rgb(var(--v-theme-border));
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-reft--add:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

/* A sheet's closeness: its name, a bar, the number, the words. */
.cs-ccap {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.cs-cline {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-cline b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

/* Data wears teal; a track is a pill. */
.cs-meter {
  flex: 1;
  max-width: 160px;
  height: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.cs-meter span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
}

.cs-num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-surface));
}

/* The reference beside a zoomed sheet, at a passport photo's size. */
.cs-zoomref {
  flex: 0 0 240px;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  overflow-y: auto;
}

.cs-secl {
  margin: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-link--start {
  align-self: flex-start;
  margin-left: 0;
  text-align: left;
}

.cs-link.cs-link--inline {
  margin-left: var(--space-1);
}

.cs-link {
  margin-left: auto;
  padding: 0;
  border: 1px solid transparent;
  background: none;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
  text-decoration: underline;
  cursor: pointer;
  white-space: nowrap;
}

/* The tile and its zoom control: the check sits top right, the magnifier
   bottom right, so a tick and a zoom never share a target. */
.cs-tile {
  position: relative;
}

.cs-mag {
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

.cs-tile:hover .cs-mag,
.cs-mag:focus-visible,
.cs-sheetimg:focus-visible + .cs-mag {
  opacity: 1;
}

/* Pointed at, it answers: a deeper backing and a ring, never a fade. Focus
   keeps the app's own focus ring and takes only the backing. */
.cs-mag:hover,
.cs-mag:focus-visible {
  background: var(--scrim-photo-strong);
}

.cs-mag:hover {
  box-shadow: 0 0 0 2px rgb(var(--v-theme-on-dark-surface));
}

/* No hover on touch: the control stays shown. */
@media (hover: none) {
  .cs-mag {
    opacity: 1;
  }
}
</style>
