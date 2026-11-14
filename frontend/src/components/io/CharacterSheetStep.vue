<template>
  <div class="cs-split">
    <div ref="mainPane" class="cs-pane" data-testid="cs-main">
      <template v-if="phase === 'describe'">
        <div class="cs-step-h">
          <h3>Describe {{ name }} and draw sheets</h3>
          <span
            >Each sheet is one person drawn six times: four passport photos
            (front, three-quarter smiling, profile, three-quarter laughing) and
            two body photos. You pick the sheet that looks right.</span
          >
        </div>

        <p
          v-if="likenessNotice && !fromReference"
          class="cs-notice"
          data-testid="cs-likeness"
        >
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
          <span
            >{{
              fromReference
                ? "Sheets from a reference need a Qwen-Image-Edit sheet workflow, and the library has none."
                : "Sheets need a MOSS-Image text-to-image workflow, and the library has none. Import one from Workflows, then come back."
            }}</span
          >
        </p>

        <div class="cs-lora">
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
          <span v-if="chosenLora && trigger" class="cs-hint"
            >Loaded into the sheet workflow, with the trigger word
            <b>{{ trigger }}</b> added to every sheet prompt. Only LoRAs for
            {{ engine.name }}, the sheet workflow's model, can be chosen.</span
          >
          <span v-else-if="chosenLora" class="cs-hint"
            >Loaded into the sheet workflow. Only LoRAs for {{ engine.name }},
            the sheet workflow's model, can be chosen.</span
          >
          <span v-else-if="fromReference" class="cs-hint"
            >Without a LoRA, the reference picture alone carries the face.</span
          >
          <span v-else class="cs-hint"
            >Without a LoRA, each sheet is a slightly different person, so you
            pick one.</span
          >
        </div>

        <div class="cs-fld">
          <AppTextarea
            v-model="form.description"
            label="Description"
            :rows="2"
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
        <div class="cs-grid2">
          <AppInput
            v-model="form.top"
            label="Top"
            placeholder="a plain oatmeal crew-neck sweater"
          />
          <AppInput
            v-model="form.trousers"
            label="Trousers"
            placeholder="dark straight jeans"
          />
        </div>
        <AppInput
          v-if="form.framing === 'full_body'"
          v-model="form.shoes"
          label="Shoes"
          placeholder="white sneakers"
        />
        <div class="cs-fld">
          <FieldLabel>Body photos</FieldLabel>
          <Segmented
            v-model="form.framing"
            aria-label="Body photos"
            :options="FRAMINGS"
            data-testid="cs-framing"
            @pick="framingTouched = true"
          />
          <span class="cs-hint" data-testid="cs-framing-hint">{{
            framingHint
          }}</span>
        </div>
        <div class="cs-grid2">
          <div class="cs-fld">
            <FieldLabel>Sheets to draw</FieldLabel>
            <Segmented
              v-model="form.count"
              aria-label="Sheets to draw"
              :options="COUNTS"
            />
            <span class="cs-hint">About a minute each.</span>
          </div>
          <div class="cs-fld">
            <FieldLabel>Sheet size</FieldLabel>
            <Segmented
              v-model="form.size"
              aria-label="Sheet size"
              :options="SIZES"
              data-testid="cs-size"
            />
            <span class="cs-hint">{{ sizeHint }}</span>
          </div>
        </div>
        <div class="cs-fld">
          <FieldLabel>Draw a</FieldLabel>
          <Segmented
            v-model="form.subject"
            aria-label="Draw a"
            :options="SUBJECTS"
          />
        </div>
        <div v-if="workflowOptions.length" class="cs-wfline">
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
      </template>

      <template v-else>
        <div class="cs-step-h">
          <h3>Keep the sheets that look right</h3>
          <span>{{ pickIntro }}</span>
        </div>
        <p
          v-if="noneHold"
          class="cs-notice cs-notice--warn"
          data-testid="cs-none-hold"
        >
          <v-icon size="16">mdi-alert-outline</v-icon>
          <span
            >None of the {{ ended.length }} sheets shows one person in all six
            photos. Drawing more usually finds one. A shorter description with
            fewer competing details helps too.</span
          >
        </p>
        <p
          v-if="noneClose"
          class="cs-notice cs-notice--warn"
          data-testid="cs-none-close"
        >
          <v-icon size="16">mdi-alert-outline</v-icon>
          <span>{{ noneClose }}</span>
        </p>
        <p
          v-if="failures.length"
          class="cs-notice cs-notice--bad"
          data-testid="cs-failed"
        >
          <v-icon size="16">mdi-alert-circle-outline</v-icon>
          <span
            >{{ failures.length }} of the runs failed: {{ failures[0] }}</span
          >
        </p>
        <ul class="cs-sheets">
          <li v-for="slot in slots" :key="slot.key">
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
                  @click="toggleKeep(slot.sheet.picture_id)"
                  @dblclick="openZoom(slot.sheet)"
                  @wheel="onTileWheel($event, slot.sheet)"
                  @keydown.z.exact="openZoom(slot.sheet)"
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
                  @click="openZoom(slot.sheet)"
                >
                  <v-icon size="16">mdi-magnify-plus-outline</v-icon>
                </button>
              </div>
              <span
                v-if="drawnFrom && slot.sheet.closeness != null"
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
              <span v-else class="cs-cap"
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
      </template>
    </div>

    <aside
      class="cs-pane cs-pane--side"
      :class="{ 'cs-pane--wide': referenceCards.length || drawnFrom }"
      data-testid="cs-rail"
    >
      <template v-if="phase === 'describe' && referenceCards.length">
        <div class="cs-fld">
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
              type="button"
              class="cs-reft cs-reft--add"
              aria-label="Pick another picture"
              data-testid="cs-reference-pick"
              @click="pickerOpen = true"
            >
              <v-icon size="14">mdi-image-plus-outline</v-icon>
            </button>
          </div>
          <span class="cs-hint">{{ referenceHint }}</span>
          <p class="cs-notice cs-notice--warn" data-testid="cs-reference-warning">
            <v-icon size="16">mdi-alert-outline</v-icon>
            <span
              >These sheets are drawn to look like the person in the picture.
              Use a real person's picture only with their agreement.</span
            >
          </p>
        </template>
      </template>
      <div
        v-else-if="phase === 'describe' && convertible"
        class="cs-item"
        data-testid="cs-convert"
      >
        <span
          >Sheets can also be drawn from a reference picture, with a copy of a
          Qwen-Image-Edit workflow that draws at the sheet's size.</span
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
      <template v-if="phase !== 'describe' && drawnFrom">
        <h3 class="cs-secl">Drawn from</h3>
        <img
          class="cs-cbig"
          :src="pictureThumbnailUrl(drawnFrom)"
          alt="The reference picture"
          data-testid="cs-drawn-from"
        />
        <span class="cs-cline"
          ><span>{{ referenceWords(drawnFrom) }}</span
          ><button
            type="button"
            class="cs-link"
            :disabled="working || phase === 'filing'"
            @click="phase = 'describe'"
          >
            Draw from another
          </button></span
        >
      </template>
      <h3 v-if="!railIsReference" class="cs-secl">{{ name }} so far</h3>
      <h3 v-else-if="keptSheets.length" class="cs-secl">Kept</h3>
      <div class="cs-rail">
        <ul
          v-if="keptSheets.length"
          class="cs-keptgrid"
          aria-label="Kept sheets"
          data-testid="cs-kept"
        >
          <li v-for="sheet in keptSheets" :key="sheet.picture_id">
            <img
              class="cs-rail__sheet"
              :src="pictureThumbnailUrl(sheet.picture_id)"
              alt=""
            />
            <b>Sheet {{ sheet.n }}</b>
          </li>
        </ul>
        <div v-if="referenceIds.length && !railIsReference" class="cs-item">
          <span class="cs-faces">
            <img
              v-for="id in referenceIds"
              :key="id"
              :src="pictureThumbnailUrl(id)"
              alt=""
            />
          </span>
          <span
            ><b>{{ name }}'s reference faces</b>. New sheets are ranked by how
            close they come to these.</span
          >
        </div>
        <div v-if="!railIsReference" class="cs-item">
          <span class="cs-row1"
            ><b>Description</b>
            <button
              v-if="phase !== 'describe'"
              type="button"
              class="cs-link"
              :disabled="working || phase === 'filing'"
              @click="phase = 'describe'"
            >
              Edit
            </button></span
          >
          <span class="cs-desc">{{
            form.description.trim() || "No description yet."
          }}</span>
        </div>
      </div>
      <div class="cs-gapbox">
        <h4 class="cs-secl">Still to come</h4>
        <div class="cs-todo">
          <span v-if="phase === 'describe'"
            ><span class="cs-n">1</span
            ><span
              ><b>Sheets</b> · 2–4 sheets of six photos, pick one</span
            ></span
          >
          <span
            ><span class="cs-n">2</span
            ><span
              ><b>Pick the set</b> · new and existing pictures, ranked
              together</span
            ></span
          >
        </div>
      </div>
    </aside>

    <ZoomPopout
      :open="!!zoomed"
      :src="zoomed ? sheetUrl(zoomed) : ''"
      :alt="zoomed ? `Sheet ${zoomed.n}` : ''"
      :caption="zoomed ? `Sheet ${zoomed.n} · ${sheetWords(zoomed)}` : ''"
      :has-prev="zoomPos > 0"
      :has-next="zoomPos >= 0 && zoomPos < zoomList.length - 1"
      :entry="zoomEntry"
      @close="closeZoom"
      @prev="stepZoom(-1)"
      @next="stepZoom(1)"
    >
      <template v-if="drawnFrom" #aside>
        <div class="cs-zoomref">
          <span class="cs-secl">Drawn from</span>
          <img
            class="cs-cbig"
            :src="pictureThumbnailUrl(drawnFrom)"
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
          @update:model-value="setKept(zoomed.picture_id, $event === 'keep')"
        />
      </template>
    </ZoomPopout>

    <!-- Mounted only while open, as the Run popup's is: it loads the
         library's facets when it opens. -->
    <PicturePicker
      v-if="pickerOpen"
      open
      subtitle="for the reference"
      @close="pickerOpen = false"
      @pick="onPicked"
    />
  </div>
</template>

<script setup>
/**
 * Step 1 of "Create a training set…" when the set is drawn rather than picked.
 *
 * Draws 2-4 character sheets with a MOSS-Image workflow (an ordinary
 * `POST /workflows/run`, so it queues and shows in Tasks like any run), ranks
 * them by how alike each sheet's own faces are, and cuts the one the user picks
 * into six pictures on the character. Where the library has a Qwen-Image-Edit
 * sheet workflow (a card with `reference_inputs`), the rail offers a second
 * way to draw: from a reference picture plus the words (#2679). The picture
 * goes in through the run's own `inputs`, the sheets are ranked by closeness
 * to it, and it stays in the rail beside them. Emits `done` with their ids, and the
 * dialog's picker takes over. The dialog owns the footer: it reads `footer`
 * and calls the actions this component exposes.
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
import { VIcon } from "vuetify/components";

import {
  buildSheetPrompt,
  rankSheets,
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
  getRunOutcome,
  getWorkflowCard,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
  sheetCopyWorkflow,
} from "../../api/workflows";
import { useTasksStore } from "../../stores/useTasksStore";
import { API_BASE_URL, appendShareToken } from "../../utils/apiClient";
import { errorMessage } from "../../utils/apiError";
import { runKey } from "../../utils/gpuMemory";
import { buildMediaUrl } from "../../utils/media";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
import Segmented from "../widgets/Segmented.vue";
import ZoomPopout from "../widgets/ZoomPopout.vue";

const props = defineProps({
  /** { id, name } of the character. */
  character: { type: Object, required: true },
  /** How many pictures the character already has. */
  pictureCount: { type: Number, default: 0 },
});

const emit = defineEmits(["done"]);

const FRAMINGS = [
  { id: "waist_up", label: "Waist-up" },
  { id: "mid_thigh", label: "Mid-thigh" },
  { id: "knees_up", label: "Knees-up" },
  { id: "full_body", label: "Full body" },
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
// Panels are filed before their faces are read; the picker ranks by face, so
// it waits for them, but not forever: a slow queue still hands over.
const FACE_WAIT_MS = 90_000;
// A sheet whose faces are still unread this long after its runs ended (no face
// model, a failed extraction, a deleted picture) stops being waited for: it
// can be chosen unscored, and the polling stops.
const SHEET_FACE_WAIT_MS = 120_000;
const HEIGHT_WORDS = /\b(tall|short|petite|height|legs?|leggy|long-legged)\b/i;

const tasks = useTasksStore();
const name = computed(() => props.character?.name || "");
const phase = ref("describe"); // describe | drawing | pick | filing
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
// The picture the next sheets are drawn from, and the one the sheets on show
// were: the rail and the ranking follow the second.
const referenceId = ref(null);
const drawnFrom = ref(null);
const pickerOpen = ref(false);
// The chosen workflow's picture inputs, as the pre-flight names them.
const workflowInputs = ref([]);
// How many faces the last ranking measured closeness against.
const rankedAgainst = ref(0);
const workflowsLoaded = ref(false);
const workflowId = ref("");
// The chosen workflow's pre-flight: its width/height inputs, and why it can't
// draw a sheet ("" while it can, null while still checking).
const workflowSizes = ref([]);
const workflowProblem = ref(null);
const referenceIds = ref([]);
const runs = ref([]); // { key, status, pictureIds, message }
const ranking = ref({}); // picture id -> RankedSheet
const order = ref([]); // sheet picture ids in the order they arrived
// The sheets to keep, in the order ticked. The best is ticked for the user
// until they tick or untick one themselves.
const kept = ref([]);
let keptTouched = false;
// The sheet in the zoom pop-out, and the wheel that opened it, if one did.
const zoomedId = ref(null);
const zoomEntry = ref(null);
const mainPane = ref(null);
const working = ref(false);
const error = ref("");
// Drawing waits for the character's stored description, so a typed one never
// overwrites it.
const loaded = ref(false);
const gaveUpOnFaces = ref(false);
let timer = null;
let ticking = false;
let runsEndedAt = 0;
let alive = true;
// What the sheets on show were drawn with. A draw with anything else starts a
// new pick: the earlier sheets are of another prompt, not more of this one.
let drawnWith = "";

const fromReference = computed(() => mode.value === "reference");
const engine = computed(() =>
  fromReference.value
    ? { family: QWEN, name: "Qwen-Image" }
    : { family: MOSS, name: "MOSS-Image" },
);
/** The rail is the reference's: its control before drawing, the picture after. */
const railIsReference = computed(() =>
  phase.value === "describe" ? fromReference.value : !!drawnFrom.value,
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

function referenceWords(id) {
  const index = referenceIds.value.indexOf(id);
  return index < 0
    ? "A picture you picked"
    : `${name.value}'s ${ORDINALS[index]} reference face`;
}

function onPicked(picture) {
  pickerOpen.value = false;
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
  if (props.pictureCount > 0) {
    return `These sheets are drawn from words, not from ${name.value}'s ${props.pictureCount} pictures. What they make is a new likeness, filed on ${name.value} and ranked beside the pictures already there.`;
  }
  return "";
});

const ended = computed(() =>
  order.value.filter((id) => ranking.value[id]?.faces_read),
);

function score(sheet) {
  return (sheet.agreement ?? -1) + (sheet.closeness ?? 0);
}

/** Drawn from a picture, closeness to it is what matters most. */
function better(a, b) {
  if (!drawnFrom.value) return score(b) - score(a);
  return (
    (b.closeness ?? -1) - (a.closeness ?? -1) ||
    (b.agreement ?? -1) - (a.agreement ?? -1)
  );
}

/** Every sheet, best first; broken ones last. */
const sheets = computed(() => {
  const scored = order.value.map((id) => ({
    picture_id: id,
    faces_read: false,
    ...(ranking.value[id] || {}),
  }));
  return scored.sort(
    (a, b) =>
      Number(!!a.broken) - Number(!!b.broken) ||
      Number(b.faces_read) - Number(a.faces_read) ||
      better(a, b),
  );
});

const holding = computed(() =>
  sheets.value.filter(
    (s) => s.faces_read && !s.broken && s.agreement_words !== "faces don't hold",
  ),
);

const runsEnded = computed(() =>
  runs.value.every((run) => ["completed", "failed"].includes(run.status)),
);

const allEnded = computed(
  () =>
    runsEnded.value &&
    (gaveUpOnFaces.value ||
      order.value.every((id) => ranking.value[id]?.faces_read)),
);

const noneHold = computed(
  () => allEnded.value && ended.value.length > 0 && !holding.value.length,
);

/** Drawn from a picture, only a sheet close to it is kept for the user: why
 * none was, or "". */
const noneClose = computed(() => {
  if (!drawnFrom.value || !allEnded.value || !holding.value.length) return "";
  if (holding.value.some((sheet) => sheet.close)) return "";
  if (!rankedAgainst.value) {
    return "No face was read in the reference picture, so the sheets can't be compared with it and none is kept for you. A reference with the face to the camera works best.";
  }
  const n = ended.value.length;
  return `None of the ${n} sheets came close to the reference, so none is kept for you. Drawing more usually finds one. A different reference picture, face to the camera, helps too.`;
});

const failures = computed(() =>
  runs.value
    .filter((run) => run.status === "failed")
    .map((run) => run.message || "the run failed."),
);

function runEnded(run) {
  return ["completed", "failed"].includes(run.status);
}

/** What each run shows, in the order the runs were started: as many places as
 * it drew sheets, or one while it draws. A sheet lands in its run's place and
 * nothing moves as the others arrive; its number is its place. */
const slots = computed(() => {
  const byId = new Map(sheets.value.map((sheet) => [sheet.picture_id, sheet]));
  const live = new Map(tasks.comfyuiRuns.map((run) => [runKey(run), run]));
  const out = [];
  for (const run of runs.value) {
    const drawn = (run.pictureIds || []).filter((id) => byId.has(id));
    for (const id of drawn) {
      out.push({ key: `sheet-${id}`, sheet: byId.get(id) });
    }
    if (drawn.length) continue;
    const progress = live.get(run.key);
    const drawing = !runEnded(run) && progress?.status === "running";
    out.push({
      key: run.key,
      label: runEnded(run) ? "didn't draw" : drawing ? "drawing" : "queued",
      percent: drawing ? Math.round(Number(progress.percent) || 0) : null,
    });
  }
  return out.map((slot, index) => ({ ...slot, n: index + 1 }));
});

const sheetNumber = computed(
  () =>
    new Map(
      slots.value
        .filter((slot) => slot.sheet)
        .map((slot) => [slot.sheet.picture_id, slot.n]),
    ),
);

const waitingRuns = computed(() => runs.value.filter((run) => !runEnded(run)));

/** A sheet with its number: its place among the slots. */
function numbered(id) {
  const sheet = sheets.value.find((s) => s.picture_id === id);
  return sheet ? { ...sheet, n: sheetNumber.value.get(id) } : null;
}

const keptSheets = computed(() =>
  kept.value.map(numbered).filter(Boolean),
);

function isKept(sheet) {
  return kept.value.includes(sheet.picture_id);
}

function setKept(id, on) {
  if (isKept({ picture_id: id }) !== on) toggleKeep(id);
}

function keepOptions(sheet) {
  const disabled = sheetLocked(sheet);
  return [
    { id: "keep", label: "Keep", icon: "check", disabled },
    { id: "drop", label: "Drop", icon: "delete-outline", disabled },
  ];
}

function toggleKeep(id) {
  keptTouched = true;
  kept.value = kept.value.includes(id)
    ? kept.value.filter((k) => k !== id)
    : [...kept.value, id];
}

const pickIntro = computed(() => {
  if (drawnFrom.value) {
    // Sheets keep their run's place, so the closest is kept, not moved first.
    return `Each sheet says how close it comes to the picture on the right. The closest is kept for you if it comes close enough. Keep the ones to use: the six photos of each become pictures of ${name.value}. Every sheet stays in the Scrapheap, used or not.`;
  }
  const base = `Each sheet says how well its own faces agree. Keep the ones to use: the six photos of each become pictures of ${name.value}. Every sheet stays in the Scrapheap, used or not, where you can restore any you like.`;
  if (!referenceIds.value.length) return base;
  return `${base} Each is also compared with ${name.value}'s reference faces: a sheet that agrees with itself but drifts from them is a different person drawn well.`;
});

function sheetWords(sheet) {
  if (!sheet.faces_read) {
    return gaveUpOnFaces.value ? "faces not read" : "reading faces…";
  }
  if (sheet.broken) return "more than one person in a photo";
  const words = [sheet.agreement_words];
  if (sheet.close != null) {
    const near = closeWords(sheet);
    // Drawn from a picture, closeness leads, with its number.
    if (drawnFrom.value) words.unshift(`${sheet.closeness.toFixed(2)} ${near}`);
    else words.push(near);
  }
  return words.join(" · ");
}

/** Closeness in words: to the picture drawn from, else to the character. */
function closeWords(sheet) {
  const whom = drawnFrom.value ? "the reference" : name.value;
  return sheet.close ? `close to ${whom}` : `drifts from ${whom}`;
}

function meterWidth(sheet) {
  return `${Math.round(Math.min(1, Math.max(0, sheet.closeness)) * 100)}%`;
}

function sheetLocked(sheet) {
  return (
    (!sheet.faces_read && !gaveUpOnFaces.value) ||
    working.value ||
    phase.value === "filing"
  );
}

const zoomed = computed(() =>
  phase.value === "describe" ? null : numbered(zoomedId.value),
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
    const name = card?.name || "This workflow";
    const sizes = (card?.defaults || []).filter((d) =>
      ["width", "height"].includes(d.input_name),
    );
    const groups = check?.groups || [];
    // The draw fills the picture inputs itself; a pinned one keeps its pin.
    const inputs = reference
      ? groups.flatMap((g) => g.picture_inputs || []).filter((i) => i.mode !== "fixed")
      : [];
    const refused = groups.some((g) =>
      (g.reasons || []).some(
        (r) => !(reference && r.code === "picture_input_unfilled"),
      ),
    );
    workflowSizes.value = sizes;
    workflowInputs.value = inputs;
    workflowProblem.value = !sizes.length
      ? `${name} has no picture size to set, so it can't draw a sheet.`
      : refused
        ? `${name} can't run as it is. Open it from Workflows to see why.`
        : reference && !inputs.length
          ? `${name} has no picture input to put the reference in.`
          : reference && !groups.some((g) => g.prompt?.positive_settable)
            ? `${name} has no prompt PixlStash can write the sheet into.`
            : "";
  } catch (e) {
    if (id !== workflowId.value) return;
    console.warn("Couldn't check the sheet workflow", id, e);
    workflowProblem.value = `Couldn't check this workflow: ${errorMessage(e)}`;
  }
});

// The pop-out steps through the sheets on show, in their places.
const zoomList = computed(() =>
  slots.value.filter((slot) => slot.sheet).map((slot) => slot.sheet.picture_id),
);
const zoomPos = computed(() => zoomList.value.indexOf(zoomedId.value));

function openZoom(sheet, entry = null) {
  zoomEntry.value = entry;
  zoomedId.value = sheet.picture_id;
}

/** Wheel up over a sheet opens it zoomed, at the point under the cursor; the
 * same gesture goes on zooming in the pop-out. Wheel down still scrolls. */
function onTileWheel(event, sheet) {
  if (event.deltaY >= 0) return;
  event.preventDefault();
  const r = event.currentTarget.getBoundingClientRect();
  openZoom(sheet, {
    deltaY: event.deltaY,
    deltaMode: event.deltaMode,
    fx: (event.clientX - r.left) / r.width,
    fy: (event.clientY - r.top) / r.height,
  });
}

function stepZoom(d) {
  const id = zoomList.value[zoomPos.value + d];
  if (id == null) return;
  zoomEntry.value = null;
  zoomedId.value = id;
}

/** Back to the pick, focus on the sheet's own zoom control. */
async function closeZoom() {
  const id = zoomedId.value;
  zoomedId.value = null;
  await nextTick();
  mainPane.value
    ?.querySelector(`[data-testid="cs-zoom-open"][data-sheet="${id}"]`)
    ?.focus();
}

watch(phase, () => {
  zoomedId.value = null;
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

async function followRuns() {
  for (const run of runs.value) {
    if (["completed", "failed"].includes(run.status)) continue;
    try {
      const outcome = await getRunOutcome(run.key);
      // A new prompt's draw dropped this run while it was being read.
      if (!runs.value.includes(run)) continue;
      run.status = outcome.status;
      run.message = outcome.message || "";
      run.pictureIds = outcome.picture_ids || [];
      for (const id of outcome.picture_ids || []) {
        if (!order.value.includes(id)) order.value.push(id);
      }
    } catch (e) {
      if (e?.response?.status === 404) {
        run.status = "failed";
        run.message = "PixlStash lost track of this run. Check Tasks for it.";
      } else {
        console.warn("Couldn't read a sheet run", run.key, e);
      }
    }
  }
}

async function tick() {
  timer = null;
  ticking = true;
  try {
    await followRuns();
    if (order.value.length) {
      // Drawn from a picture, the sheets are measured against that picture.
      const answer = drawnFrom.value
        ? await rankSheets(props.character.id, order.value, [drawnFrom.value])
        : await rankSheets(props.character.id, order.value);
      rankedAgainst.value = answer.references || 0;
      ranking.value = Object.fromEntries(
        answer.sheets.map((sheet) => [sheet.picture_id, sheet]),
      );
    }
  } catch (e) {
    console.warn("Couldn't rank the character sheets", e);
  } finally {
    ticking = false;
  }
  if (!alive) return;
  if (!runsEnded.value) runsEndedAt = 0;
  else if (!runsEndedAt) runsEndedAt = Date.now();
  if (runsEndedAt && Date.now() - runsEndedAt > SHEET_FACE_WAIT_MS) {
    gaveUpOnFaces.value = true;
  }
  // Drawn from a picture, only a sheet close to it is kept for the user.
  const best = drawnFrom.value
    ? holding.value.find((sheet) => sheet.close)
    : holding.value[0];
  if (!keptTouched && !kept.value.length && best) {
    kept.value = [best.picture_id];
  }
  if (allEnded.value) {
    if (phase.value === "drawing") phase.value = "pick";
    return;
  }
  schedule();
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
  try {
    const description = form.description.trim();
    if (!savedDescription.value && description) {
      await patchCharacter(props.character.id, { description });
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
    const drawing = JSON.stringify([
      sheet.prompt,
      workflow,
      lora?.sha256 || "",
      weight,
      reference,
    ]);
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
    const started = (answer?.prompts || []).filter((p) => runKey(p));
    if (!started.length) {
      throw new Error(
        answer?.error ||
          "The workflow can't run as it is. Open it from Workflows to see why.",
      );
    }
    if (drawing !== drawnWith) {
      // The sheets on show are of another prompt: they stay in the Scrapheap.
      startOver();
      drawnWith = drawing;
      drawnFrom.value = reference;
    }
    runs.value.push(
      ...started.map((p) => ({
        key: runKey(p),
        status: p.status === "failed" ? "failed" : "queued",
        message: "",
      })),
    );
    phase.value = "drawing";
    gaveUpOnFaces.value = false;
    schedule();
  } catch (e) {
    error.value = `Couldn't start the sheets. ${errorMessage(e, "Please try again.")}`;
  } finally {
    working.value = false;
  }
}

/** Forget every sheet and run: a new pick starts empty. */
function startOver() {
  runs.value = [];
  order.value = [];
  ranking.value = {};
  kept.value = [];
  keptTouched = false;
  runsEndedAt = 0;
  drawnWith = "";
  drawnFrom.value = null;
  rankedAgainst.value = 0;
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
  const keep = keptSheets.value.map((sheet) => sheet.picture_id);
  if (working.value || !keep.length) return;
  working.value = true;
  error.value = "";
  phase.value = "filing";
  let ids;
  try {
    ({ picture_ids: ids } = await useSheets(props.character.id, keep));
  } catch (e) {
    phase.value = "pick";
    error.value = `Couldn't use the sheets. ${errorMessage(e, "Please try again.")}`;
    working.value = false;
    return;
  }
  // Only the panels' faces may still be unread. A failure from here on is no
  // reason to offer the cut again.
  const deadline = Date.now() + FACE_WAIT_MS;
  while (alive && Date.now() < deadline) {
    try {
      const answer = await rankSheets(props.character.id, ids);
      if (answer.sheets.every((sheet) => sheet.faces_read)) break;
    } catch (e) {
      console.warn("Couldn't read the new pictures' faces", ids, e);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  working.value = false;
  if (!alive) return;
  // The sheets are used: a return through the step row starts a new draw.
  startOver();
  phase.value = "describe";
  emit("done", ids);
}

const canDraw = computed(
  () =>
    loaded.value &&
    !!workflowId.value &&
    workflowProblem.value === "" &&
    // The picture says who; in words alone the description has to.
    (fromReference.value ? !!referenceId.value : !!form.description.trim()) &&
    phase.value !== "filing",
);

/** Kept sheets whose faces look like different people, as pairs of numbers.
 * Without a LoRA each sheet is its own person, so keeping two usually mixes
 * faces; with one, several sheets can be the same person and pass. */
const mixedPairs = computed(() => {
  const kept = keptSheets.value;
  const pairs = [];
  for (const sheet of kept) {
    for (const id of sheet.different_from || []) {
      const other = kept.find((k) => k.picture_id === id);
      if (other && sheet.n < other.n) pairs.push([sheet.n, other.n]);
    }
  }
  return pairs;
});

const mixedWarning = computed(() => {
  const pairs = mixedPairs.value;
  if (!pairs.length) return "";
  if (pairs.length === 1) {
    return `Sheets ${pairs[0][0]} and ${pairs[0][1]} look like different people; using both mixes faces.`;
  }
  return "Kept sheets look like different people; using them mixes faces.";
});

const keptWords = computed(() => {
  const ns = keptSheets.value.map((sheet) => sheet.n);
  if (ns.length === 1) return `Sheet ${ns[0]}`;
  return `Sheets ${ns.slice(0, -1).join(", ")} and ${ns[ns.length - 1]}`;
});

const keepLabel = computed(() => {
  const count = keptSheets.value.length;
  if (count === 0) return "Use a sheet";
  if (count === 1) return `Use sheet ${keptSheets.value[0].n}`;
  return `Use ${count} sheets`;
});

const footer = computed(() => {
  const n = form.count;
  if (phase.value === "describe") {
    return {
      lead: `Pictures are filed on ${name.value}`,
      back: order.value.length
        ? { label: "Back to the sheets", run: () => (phase.value = "pick") }
        : null,
      primary: {
        label: `Draw ${n} ${n === 1 ? "sheet" : "sheets"}`,
        icon: "draw",
        enabled: canDraw.value,
        run: draw,
      },
    };
  }
  if (phase.value === "filing") {
    return {
      lead: "Cutting the sheets into pictures and reading their faces…",
      primary: { label: keepLabel.value, enabled: false, run: use },
    };
  }
  const waiting = waitingRuns.value.length;
  return {
    // Warned, not refused: the owner may want two looks of one character.
    warn: !!mixedWarning.value,
    lead:
      mixedWarning.value ||
      (waiting
        ? `Drawing ${waiting} more, about a minute each.`
        : keptSheets.value.length
          ? `${keptWords.value} ${keptSheets.value.length === 1 ? "becomes" : "become"} ${name.value}'s pictures; the sheets stay in the Scrapheap.`
          : "Keep the sheets to use. Every sheet stays in the Scrapheap."),
    // The rail's Edit goes back to the form too; the footer offers it only
    // when no sheet is worth using.
    back: noneHold.value
      ? { label: "Edit the description", run: () => (phase.value = "describe") }
      : null,
    secondary: {
      label: `Draw ${n} more`,
      enabled: canDraw.value && !working.value,
      run: draw,
    },
    primary: {
      label: keepLabel.value,
      enabled:
        keptSheets.value.length > 0 &&
        keptSheets.value.every((s) => s.faces_read || gaveUpOnFaces.value) &&
        !working.value,
      run: use,
    },
  };
});

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

async function load() {
  const id = props.character.id;
  const [character, adapters, cards, references] = await Promise.allSettled([
    getCharacter(id),
    listAdapters({ characterId: id }),
    listWorkflowCards(),
    getReferencePictures(id),
  ]);
  if (!alive) return;
  if (character.status === "fulfilled") {
    savedDescription.value = (character.value?.description || "").trim();
    if (!form.description) form.description = savedDescription.value;
  }
  if (adapters.status === "fulfilled") {
    loras.value = [...adapters.value].sort((a, b) =>
      String(b.added_at || "").localeCompare(String(a.added_at || "")),
    );
    // The newest LoRA that loads into the sheet workflow is preselected.
    loraSha.value = fittingLora();
  }
  if (references.status === "fulfilled") {
    referenceIds.value = (references.value?.reference_picture_ids || []).slice(
      0,
      4,
    );
    referenceId.value = referenceIds.value[0] ?? null;
  }
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
  workflowsLoaded.value = true;
  loaded.value = character.status === "fulfilled";
  if (!loaded.value) {
    error.value = `Couldn't read ${name.value}. Close and open the dialog to try again.`;
  }
  for (const result of [character, adapters, cards, references]) {
    if (result.status === "rejected") {
      console.warn("Couldn't load part of the sheet form", result.reason);
    }
  }
}

onMounted(load);
onBeforeUnmount(() => {
  alive = false;
  stopPolling();
});

// While a sheet is zoomed the dialog does not take Escape; the zoom does.
// The picture picker is a dialog over this one, and takes Escape the same way.
const zooming = computed(() => !!zoomed.value || pickerOpen.value);

defineExpose({ footer, error, working, loraName, phase, zooming });
</script>

<style scoped>
.cs-split {
  flex: 1;
  min-height: 0;
  display: flex;
}

.cs-pane {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.cs-pane--side {
  flex: 0 0 280px;
  gap: var(--space-4);
  border-left: 1px solid rgb(var(--v-theme-divider));
}

.cs-pane--wide {
  flex-basis: 320px;
}

.cs-step-h {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.cs-step-h h3 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.cs-step-h span,
.cs-hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-hint {
  font-size: var(--text-2xs);
}

.cs-hint b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cs-fld {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cs-grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}

.cs-lora {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
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

.cs-wfline {
  max-width: 360px;
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

.cs-sheets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-4) var(--space-3);
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

.cs-rail {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.cs-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-row1 {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.cs-link--start {
  align-self: flex-start;
  margin-left: 0;
  text-align: left;
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

/* Kept sheets two across; more rows scroll with the rail. */
.cs-keptgrid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}

.cs-keptgrid li {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  font-size: var(--text-2xs);
}

.cs-keptgrid b {
  font-weight: var(--weight-medium);
}

.cs-desc {
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

.cs-rail__sheet {
  width: 100%;
  aspect-ratio: 5 / 3;
  object-fit: cover;
  border-radius: var(--radius-md);
}

.cs-faces {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-1);
}

.cs-faces img {
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  border-radius: var(--radius-sm);
}

.cs-gapbox {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.cs-todo {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.cs-todo > span {
  display: flex;
  gap: var(--space-2);
  align-items: baseline;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cs-todo b {
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.cs-n {
  min-width: 12px;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
</style>
