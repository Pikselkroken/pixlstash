<template>
  <!-- The body of the sidebar's Edit tab (#1381, design "Slim Form"): pick an
       edit workflow, say what should change, run it. The lightbox stays on the
       picture; the result joins its stack and the tab says where it went. -->
  <div class="edit-pane">
    <!-- No ComfyUI: nothing to run, but the picture can still be turned,
         cropped and filtered. -->
    <div v-if="!comfyuiConfigured" class="edit-scroll">
      <slot name="quick-changes" />
    </div>
    <div v-else-if="loading" class="edit-note">Reading your workflows…</div>
    <div v-else-if="loadError" class="edit-note">{{ loadError }}</div>

    <div v-else-if="!cards.length" class="edit-empty">
      <RunReasonNotice
        v-if="packReason"
        class="edit-missing-notice edit-pack-notice"
        :reason="packReason"
        on-dark
        @retry="reloadCards"
      />
      <p v-if="unrunnable" class="edit-empty-lead">
        None of your edit workflows can run as they stand. Open one in
        Workflows and press Run… to see why.
      </p>
      <p v-else class="edit-empty-lead">
        None of your workflows takes a single picture as its input, so there is
        nothing to run this picture through.
      </p>
      <p class="edit-note">
        An image-to-image, inpaint or outpaint workflow with one open picture
        input shows up here.
      </p>
      <AppButton
        size="sm"
        block
        on-dark
        @click="router.push({ name: 'workflows' })"
      >
        Open Workflows
      </AppButton>
      <slot name="quick-changes" />
    </div>

    <template v-else>
      <div class="edit-scroll">
        <!-- Workflows dropped for nodes a known pack provides (#2054): the
             install would bring them back, so the tab offers it. -->
        <RunReasonNotice
          v-if="packReason"
          class="edit-missing-notice edit-pack-notice"
          :reason="packReason"
          on-dark
          @retry="reloadCards"
        />
        <!-- Where the edit goes: the mark tools head the tab, then the
             selection's refinements. Without a selection the whole picture
             changes. -->
        <template v-if="paint">
          <PaintToolbox :paint="paint" />
          <div class="section-label section-label--on-dark edit-sec">
            <span>{{ hasSelection ? "Refine selection" : "Where" }}</span>
            <span v-if="hasSelection" class="edit-sec-end">
              <button
                class="edit-sec-act"
                type="button"
                :aria-pressed="paint.showMask.value ? 'true' : 'false'"
                aria-label="Show the mask in black and white"
                @click="paint.toggleMask()"
              >
                <Tooltip
                  text="Show the mask in black and white, as the workflow gets it"
                  activator="parent"
                  :describe="false"
                />
                <v-icon size="16">{{
                  paint.showMask.value ? "mdi-eye-off-outline" : "mdi-eye-outline"
                }}</v-icon>
              </button>
              <button
                class="edit-sec-act edit-sec-act--clear"
                type="button"
                :aria-keyshortcuts="paint.tool.value ? undefined : 'Escape'"
                @click="paint.clear()"
              >
                Clear
                <!-- With a tool in hand Esc puts the tool down first. -->
                <kbd v-if="!paint.tool.value" class="edit-kbd" aria-hidden="true"
                  >Esc</kbd
                >
              </button>
            </span>
          </div>
          <p v-if="!hasSelection" class="edit-note edit-note--lead">
            Mark where with the tools above. Without a selection the whole
            picture changes.
          </p>
          <template v-else>
            <label
              v-for="row in refineRows"
              :key="row.key"
              class="edit-slider"
            >
              <span>{{ row.label }}</span>
              <input
                :value="paint.refine[row.key]"
                type="range"
                :min="row.min"
                :max="row.max"
                step="1"
                @input="paint.setRefine(row.key, $event.target.value)"
              />
              <output
                >{{ row.key === "grow" && paint.refine.grow > 0 ? "+" : ""
                }}{{ paint.refine[row.key] }} px</output
              >
            </label>
            <p v-if="paint.maskError.value" class="edit-note" role="alert">
              {{ paint.maskError.value }}
            </p>
          </template>
        </template>

        <template v-if="applyToIds.length > 1">
          <div class="section-label section-label--on-dark edit-sec">
            <span>Apply to</span>
          </div>
          <Segmented
            :model-value="hasSelection ? 'this' : applyTo"
            full
            aria-label="Apply to"
            :options="applyToOptions"
            @update:model-value="(value) => (applyTo = value)"
          />
          <p v-if="hasSelection" class="edit-note">
            A hand selection stays on the picture it was drawn on. Clear it to
            run on all {{ applyToIds.length }}.
          </p>
        </template>

        <div class="section-label section-label--on-dark edit-sec">
          <span :id="workflowLabelId">Runs with</span>
          <span class="edit-sec-end">
            <span v-if="workflowId === rememberedKey" class="edit-aside"
              >last used</span
            >
            <!-- The Recipe tab's "Open", on the card this tab would run. -->
            <button
              v-if="workflowId"
              class="edit-sec-act"
              type="button"
              @click="openInWorkflows"
            >
              <Tooltip
                text="Show this workflow in the Workflows view"
                activator="parent"
                :describe="false"
              />
              Open
              <v-icon size="14">mdi-chevron-right</v-icon>
            </button>
          </span>
        </div>
        <!-- The app's one menu in its on-dark skin, not a native <select>:
             an OS-drawn popup ignores the theme (PluginSelect was a <select>
             briefly for the same reason). A below-opening menu's min-width is
             capped at its activator's, so the oversized floor makes the menu
             exactly as wide as the field. -->
        <v-menu
          v-model="pickerOpen"
          location="bottom start"
          :offset="4"
          :min-width="10000"
          @after-leave="onPickerAfterLeave"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="withRef(menuProps, (el) => (pickerTriggerRef = el))"
              class="edit-select"
              type="button"
              :aria-labelledby="`${workflowLabelId} ${pickerValueId}`"
            >
              <span :id="pickerValueId" class="edit-select-name">{{
                workflowId
                  ? chosenCard?.name || "Untitled workflow"
                  : "Choose a workflow"
              }}</span>
              <span class="edit-kind">{{ chosenCard?.type_label }}</span>
              <v-icon class="edit-select-chevron" aria-hidden="true"
                >mdi-chevron-down</v-icon
              >
            </button>
          </template>
          <div
            class="ctx-menu ctx-menu--on-dark"
            role="menu"
            aria-label="Workflow"
            tabindex="-1"
            @keydown="onMenuKeydown"
          >
            <button
              v-for="card in menuCards"
              :key="card.id"
              class="ctx-item edit-option"
              type="button"
              role="menuitemradio"
              :aria-checked="card.id === workflowId ? 'true' : 'false'"
              @click="choose(card.id)"
            >
              <!-- The ellipsis: this one opens the Run popup. -->
              <span class="ctx-label-text"
                >{{ card.name || "Untitled workflow"
                }}{{ fillInPopup.has(card.id) ? "…" : "" }}</span
              >
              <!-- Listed though it cannot run yet: a model download away. -->
              <span v-if="missing.has(card.id)" class="edit-missing">
                <Tooltip
                  :text="missingTip(card.id)"
                  activator="parent"
                  :describe="false"
                />
                <v-icon size="14" aria-hidden="true">mdi-alert-outline</v-icon>
                <span class="visually-hidden">, {{ missingTip(card.id) }}</span>
              </span>
              <span class="visually-hidden">, </span>
              <span class="edit-kind">{{ card.type_label }}</span>
            </button>
            <div class="ctx-sep" role="separator"></div>
            <p class="edit-menu-note">
              Upscalers are not listed here. Use More options… for those.
            </p>
          </div>
        </v-menu>
        <!-- The Run popup's notice: each missing file, with its download
             where the server can fetch it. Run waits for them. -->
        <RunReasonNotice
          v-if="chosenMissing"
          class="edit-missing-notice"
          :reason="chosenMissing"
        />
        <p v-if="hasSelection && chosenCard && !usesMask" class="edit-note">
          {{ chosenCard.type_label }} workflows do not use a mask, so this
          would change the whole picture. Choose an Inpaint workflow.
        </p>

        <div class="section-label section-label--on-dark edit-sec">
          <label :for="instructionFieldId">{{ promptHelp.label }}</label>
          <button
            class="edit-sec-act edit-sec-act--help"
            type="button"
            :aria-expanded="helpOpen ? 'true' : 'false'"
            :aria-controls="helpId"
            @click="helpOpen = !helpOpen"
          >
            <v-icon size="14">mdi-help-circle-outline</v-icon>
            How to write it
          </button>
        </div>
        <textarea
          :id="instructionFieldId"
          v-model="instruction"
          class="edit-textarea"
          rows="4"
          :placeholder="promptHelp.placeholder"
          :aria-describedby="shortcutHintId"
          @keydown="onInstructionKeydown"
        ></textarea>
        <div v-show="helpOpen" :id="helpId" class="edit-help">
          <p v-for="(tip, index) in promptHelp.tips" :key="index">{{ tip }}</p>
        </div>

        <label class="edit-check">
          <input v-model="stack" type="checkbox" />
          Stack with the original
        </label>

        <slot name="quick-changes" />

        <template v-if="runHere">
          <div class="section-label section-label--on-dark edit-sec">
            <span>{{ runHeading }}</span>
            <span v-if="run.status === 'running'" class="edit-aside"
              >{{ comfyuiProgressPercent }}%</span
            >
          </div>
          <template v-if="run.status === 'running'">
            <div
              class="edit-bar"
              role="progressbar"
              aria-label="Edit progress"
              :aria-valuenow="comfyuiProgressPercent"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <i :style="{ width: `${comfyuiProgressPercent}%` }"></i>
            </div>
            <p class="edit-note">
              <template v-if="run.count > 1"
                >Running on {{ run.count }} pictures. </template
              >You can keep browsing.
              {{
                run.stack
                  ? "The result joins the original's stack when it lands."
                  : "The result is saved to your library when it lands."
              }}
            </p>
          </template>
          <p v-else-if="run.status === 'failed'" class="edit-note" role="alert">
            {{ run.message }}
          </p>
          <div v-else class="edit-last">
            <img
              v-if="run.resultId"
              class="edit-last-thumb"
              :src="pictureThumbnailUrl(run.resultId)"
              alt=""
            />
            <div class="edit-last-text">
              <div class="edit-last-instruction">
                {{ run.instruction ? `“${run.instruction}”` : "No instruction" }}
              </div>
              <div class="edit-note">
                {{ run.workflowName }}
                <template v-if="run.stack">· added to the original's stack</template>
              </div>
              <button
                v-if="run.stack && run.resultId"
                type="button"
                class="edit-link"
                @click="showResult"
              >
                Show it
              </button>
              <p v-else-if="run.stack && run.lookedInVain" class="edit-note">
                It has not reached the stack yet. It will be there once it is
                imported.
              </p>
              <p v-else-if="run.stack" class="edit-note">
                Adding it to the stack…
              </p>
            </div>
          </div>
        </template>
      </div>

      <div class="edit-foot">
        <p v-if="submitError" class="edit-note" role="alert">
          {{ submitError }}
        </p>
        <AppButton
          on-dark
          variant="primary"
          size="sm"
          block
          :loading="submitting"
          :disabled="!!chosenMissing"
          @click="submit"
        >
          <v-icon v-if="!submitting" size="16">mdi-play</v-icon>
          {{
            runsOnAll ? `Run on ${applyToIds.length} pictures` : "Run edit"
          }}
        </AppButton>
        <div class="edit-foot-row">
          <button type="button" class="edit-link" @click="onMoreOptions()">
            More options…
          </button>
          <span :id="shortcutHintId" class="edit-note">Ctrl + Enter</span>
        </div>
        <!-- The selection's undo, which the floating toolbox used to hold. -->
        <button
          v-if="hasSelection"
          type="button"
          class="edit-link edit-undo"
          aria-keyshortcuts="Control+Z"
          @click="paint.undo()"
        >
          Undo stroke
          <kbd class="edit-kbd" aria-hidden="true">Ctrl Z</kbd>
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
/**
 * The Edit tab of the lightbox sidebar (#1381).
 *
 * Built to the "Slim Form" design: three fields and a button. Everything past
 * the instruction (LoRAs, seed, size) stays in the Run popup, one click away
 * behind *More options…*, which opens it with this picture, this workflow and
 * the typed instruction filled in.
 *
 * **Which cards count.** Image to Image, Inpaint and Outpaint only - the card
 * types whose graph starts from a picture and takes an instruction. Upscalers
 * are left out (a form with a dead text box) and stay on *Use as input for…*.
 * Hidden cards stay out because the grid's default list leaves them out.
 *
 * **Only cards that would run, or would after a download.** Each edit card is
 * pre-flighted against the open picture, the same question Run asks, and one
 * that answers with a reason is dropped: a missing node, a UI-format file. A
 * card whose only reason is missing models (#2055) is listed with a warning
 * glyph, since a download fixes it: chosen, it shows the Run popup's notice
 * with each file's download, and Run waits. A download landing asks those
 * cards again; one that now answers with another reason is dropped, and one
 * whose re-check failed keeps waiting. A card whose only reason is an open
 * picture input (a reference beside the open picture) is listed with an
 * ellipsis and never becomes the tab's card: an instruction alone cannot run
 * it, so choosing it opens the Run popup, which can fill it. A node the server
 * replaces (a seed node) or a LoRA it bypasses is not a reason, so those cards
 * stay. A ComfyUI that cannot be asked (not set up,
 * not running) is no verdict on any one card, so every card stays and the run
 * says why; so does a card whose pre-flight itself failed.
 *
 * **After a run the lightbox stays put.** The run is handed to the grid
 * WITHOUT a source picture, because a source picture is what makes the grid
 * step the lightbox to the newest stack member when the output lands - the
 * Run popup's behaviour, and the opposite of this design. *Show it* does that
 * step when asked.
 *
 * One run is shown at a time. A second Run while the first is going is queued
 * by ComfyUI as usual, and the tab follows the newest.
 */
import { computed, onBeforeUnmount, ref, useId, watch } from "vue";
import { useRouter } from "vue-router";
import RunReasonNotice from "../io/RunReasonNotice.vue";
import AppButton from "../widgets/AppButton.vue";
import PaintToolbox from "./PaintToolbox.vue";
import Segmented from "../widgets/Segmented.vue";
import Tooltip from "../widgets/Tooltip.vue";
import {
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
} from "../../api/workflows";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { errorMessage } from "../../utils/apiError";
import {
  MISSING_MODELS,
  MISSING_NODES,
  MODELS_DOWNLOADING,
  PICTURE_INPUT_UNFILLED,
  UNCHECKED_CODES,
  readReason,
} from "../../utils/runReasons";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import { runTaskLabel } from "../../utils/runTaskLabel";
import { withRef } from "../../utils/withRef";
import { selectNewestStackMember } from "../../utils/stack";
import { stackMemberIds } from "../../utils/stackMembers";

const props = defineProps({
  /** The open picture. */
  pictureId: { type: [Number, String], default: null },
  /** Whether the tab is the one on screen; the card list is read on first show. */
  active: { type: Boolean, default: false },
  /** The grid's ComfyUI progress, `{status, message}`. */
  comfyuiProgress: { type: Object, default: null },
  comfyuiProgressPercent: { type: Number, default: 0 },
  /** The lightbox's Paint selection (`usePaintSelection`), or null. */
  paint: { type: Object, default: null },
  /** Every picture the grid's *Edit…* named, when it named several. */
  applyToIds: { type: Array, default: () => [] },
  /**
   * Whether this machine has a ComfyUI. Without one the tab holds only the
   * `quick-changes` slot (rotate, crop, filters) and reads no workflows.
   */
  comfyuiConfigured: { type: Boolean, default: true },
});

const emit = defineEmits(["show-picture", "more-options", "running"]);
const router = useRouter();

/** The card types this tab lists, as `workflow_type` spells them. */
const EDIT_TYPES = new Set(["img2img", "inpaint", "outpaint"]);
/**
 * The ones whose graph reads a mask (`workflow_type` says so by its nodes).
 * Not outpaint: it makes its own mask from the padding it adds.
 */
const MASK_TYPES = new Set(["inpaint"]);

const refineRows = [
  { key: "grow", label: "Grow", min: -100, max: 100 },
  { key: "feather", label: "Feather", min: 0, max: 100 },
  { key: "fill_holes", label: "Fill holes", min: 0, max: 200 },
];

const LAST_KEY_PREFIX = "pixlstash:editTabWorkflow:";
// The last workflow run on a selection, which a new selection goes back to.
const LAST_MASKED_KEY_PREFIX = "pixlstash:editTabMaskedWorkflow:";

/**
 * What the instruction box asks for. An inpaint run regenerates only the
 * selection, so the prompt describes what belongs there; an edit runs the
 * whole picture, where an edit model (Qwen Image Edit, Flux Kontext) follows
 * an instruction and an ordinary checkpoint reads a description.
 */
const PROMPT_HELP = {
  masked: {
    label: "What goes there?",
    placeholder: "A red knitted scarf, soft window light",
    tips: [
      "Describe what should be there, not the change: “a red knitted scarf”, not “add a scarf”.",
      "Name the look so it blends in: lighting, material, style.",
      "Leave out the rest of the picture; the workflow keeps it.",
      "Left empty, the workflow's own prompt is used.",
    ],
  },
  edit: {
    label: "What should change?",
    placeholder: "Make the sky stormy",
    tips: [
      "Say the change in plain words, one at a time: “make the sky stormy”, “turn the jacket red”.",
      "Say what must stay if it matters: “keep her face the same”.",
      "An ordinary checkpoint reads this as the whole picture, not a change: describe the finished picture instead.",
      "To change only part of it, mark it with the tools beside the picture and choose an Inpaint workflow.",
    ],
  },
};

const workflowLabelId = useId();
const pickerValueId = useId();
const instructionFieldId = useId();
const shortcutHintId = useId();
const helpId = useId();

const runDialog = useRunDialogStore();
const libraries = useLibrariesStore();
const tasksStore = useTasksStore();

const cards = ref([]);
// Edit cards the pre-flight turned away, so the empty state can say so.
const unrunnable = ref(0);
// Cards that run once the Run popup fills their other picture inputs.
const fillInPopup = ref(new Set());
// Cards that run once their models are downloaded: id -> the missing_models
// reason, as RunReasonNotice reads it.
const missing = ref(new Map());
// One missing_nodes reason for every card dropped only for nodes a known pack
// provides, its packs merged by id; null when there is none.
const packReason = ref(null);
const loaded = ref(false);
const loading = ref(false);
const loadError = ref("");
const workflowId = ref("");
const rememberedKey = ref("");
// Kept across filmstrip steps on purpose, so one instruction can be run on
// several pictures in a row. It is never filled from the picture's recipe.
const instruction = ref("");
const stack = ref(true);
const helpOpen = ref(false);
const submitting = ref(false);
const submitError = ref("");
/**
 * The run this tab started last, or null.
 * `{sourceId, workflowName, instruction, stack, status, message, beforeIds,
 * resultId, lookedInVain}` - `status` is running | done | failed.
 */
const run = ref(null);

const pickerOpen = ref(false);
const pickerTriggerRef = ref(null);
// The chosen row leaves with the menu and takes focus to <body>; the field is
// refocused once the close transition ends, as PluginSelect does.
let refocusPicker = false;

const chosenCard = computed(
  () => cards.value.find((card) => card.id === workflowId.value) || null,
);

const chosenMissing = computed(
  () => missing.value.get(workflowId.value) || null,
);

function missingTip(key) {
  const models = missing.value.get(key)?.models || [];
  const gone = models.filter((model) => !model.downloading).length;
  if (!gone) return "Still downloading a model it needs";
  return `${gone} ${gone === 1 ? "model" : "models"} missing`;
}

const hasSelection = computed(() => !!props.paint?.hasSelection.value);
const usesMask = computed(() => MASK_TYPES.has(chosenCard.value?.type));
const promptHelp = computed(() =>
  hasSelection.value && usesMask.value ? PROMPT_HELP.masked : PROMPT_HELP.edit,
);

// With a selection the workflows that read it come first.
const menuCards = computed(() =>
  hasSelection.value
    ? [
        ...cards.value.filter((card) => MASK_TYPES.has(card.type)),
        ...cards.value.filter((card) => !MASK_TYPES.has(card.type)),
      ]
    : cards.value,
);

// *Apply to*: this picture, or every one the grid's *Edit…* named. A hand
// selection belongs to its picture, so it holds the choice on this one.
const applyTo = ref("all");
const applyToOptions = computed(() => [
  { id: "this", label: "This picture" },
  {
    id: "all",
    label: `${props.applyToIds.length} selected`,
    disabled: hasSelection.value,
  },
]);
const runsOnAll = computed(
  () =>
    props.applyToIds.length > 1 &&
    applyTo.value === "all" &&
    !hasSelection.value,
);
watch(
  () => props.applyToIds,
  () => {
    applyTo.value = "all";
  },
);

// Whether the reader has picked a card by hand since the list loaded, which the
// late library key below must not overrule.
let choseByHand = false;

// The card a selection moved the tab off, to go back to when it is cleared;
// empty once the reader chooses a card themselves.
let beforeSelection = "";

/** The workflow a selection runs with: the last one run on one, else the first. */
function maskedCard() {
  const last = readRemembered(LAST_MASKED_KEY_PREFIX);
  const masked = cards.value.filter((card) => MASK_TYPES.has(card.type));
  const usable = masked.filter((card) => runsHere(card.id));
  // One a download would fix beats staying on a card that ignores the mask.
  return (
    usable.find((card) => card.id === last) ||
    usable[0] ||
    masked.find((card) => missing.value.has(card.id)) ||
    null
  );
}

function moveToMasked() {
  if (usesMask.value) return;
  const masked = maskedCard();
  if (!masked) return;
  beforeSelection = workflowId.value;
  workflowId.value = masked.id;
}

// A selection moves the tab onto a workflow that reads it, even off a card
// chosen by hand before there was one: that choice was for the whole picture.
// Clearing it goes back, unless the reader has chosen since.
watch(hasSelection, (has) => {
  if (has) {
    moveToMasked();
  } else if (beforeSelection) {
    if (runsHere(beforeSelection)) workflowId.value = beforeSelection;
    beforeSelection = "";
  }
});

function choose(key) {
  pickerOpen.value = false;
  if (fillInPopup.value.has(key)) {
    // The Run popup takes focus; handing it back to the picker would steal it.
    onMoreOptions(key, true);
    return;
  }
  refocusPicker = true;
  choseByHand = true;
  beforeSelection = "";
  workflowId.value = key;
}

function onPickerAfterLeave() {
  if (!refocusPicker) return;
  refocusPicker = false;
  pickerTriggerRef.value?.focus?.();
}

const libraryKey = computed(() => libraries.activeLibrary?.uuid || "");

// The library list can arrive after the cards did (a lightbox opened from a
// deep link), which moves the key from "no library" to the real one. Read the
// remembered card again under it, or the first run would be remembered under
// one key and looked up under another. A library SWITCH reloads the page.
watch(libraryKey, () => {
  if (!loaded.value) return;
  rememberedKey.value = readRemembered();
  if (!choseByHand && runsHere(rememberedKey.value)) {
    workflowId.value = rememberedKey.value;
  }
  // A card chosen by hand stays chosen, with a selection up or not.
  if (hasSelection.value && !choseByHand) moveToMasked();
});

// The run belongs to the picture it started from (and the picture it made);
// stepping to any other picture shows that picture's form, not this run.
const runHere = computed(() => {
  const current = run.value;
  if (!current) return null;
  const here = String(props.pictureId);
  return String(current.sourceId) === here || String(current.resultId) === here
    ? current
    : null;
});

// A refusal is about the picture it was for.
watch(
  () => props.pictureId,
  () => {
    submitError.value = "";
  },
);

const runHeading = computed(() => {
  if (run.value?.status === "running") return "Running";
  if (run.value?.status === "failed") return "Edit failed";
  return "Last edit";
});

function readRemembered(prefix = LAST_KEY_PREFIX) {
  try {
    return window.localStorage?.getItem(`${prefix}${libraryKey.value}`) || "";
  } catch (err) {
    console.warn("Could not read the Edit tab's last workflow:", err);
    return "";
  }
}

/** The card just run; one run on a selection is also the next selection's. */
function remember(key, masked) {
  rememberedKey.value = key;
  try {
    window.localStorage?.setItem(`${LAST_KEY_PREFIX}${libraryKey.value}`, key);
    if (masked) {
      window.localStorage?.setItem(
        `${LAST_MASKED_KEY_PREFIX}${libraryKey.value}`,
        key,
      );
    }
  } catch (err) {
    console.warn("Could not remember the Edit tab's workflow:", err);
  }
}

async function loadCards() {
  if (loaded.value || loading.value) return;
  loading.value = true;
  loadError.value = "";
  try {
    // The Workflows grid's own list: one card per workflow, and no
    // one-offs. Every variant and every pulled experiment made the picker too
    // long to be any use.
    const { cards: all } = await listWorkflowCards();
    const edits = all.filter((card) => EDIT_TYPES.has(card.type));
    const landed = tasksStore.downloadsLanded;
    const runs = await Promise.all(edits.map(wouldRun));
    cards.value = edits.filter((_, index) => listed(runs[index]));
    packReason.value = mergedPackReason(
      runs.filter((run) => run?.code === MISSING_NODES),
    );
    fillInPopup.value = new Set(
      edits.filter((_, index) => runs[index] === FILL).map((card) => card.id),
    );
    missing.value = new Map(
      edits.flatMap((card, index) =>
        runs[index]?.code === MISSING_MODELS ? [[card.id, runs[index]]] : [],
      ),
    );
    unrunnable.value = edits.length - cards.value.length;
    rememberedKey.value = readRemembered();
    workflowId.value = runsHere(rememberedKey.value)
      ? rememberedKey.value
      : defaultKey();
    // Drawn before the tab was first shown.
    if (hasSelection.value) moveToMasked();
    loaded.value = true;
    // A download that landed during the read may postdate its answers.
    if (tasksStore.downloadsLanded !== landed) void recheckMissing();
  } catch (err) {
    loadError.value = errorMessage(err, "Could not read your workflows.");
  } finally {
    loading.value = false;
  }
}

const FILL = "fill";

/** Whether a `wouldRun` answer keeps the card in the picker. */
function listed(run) {
  return Boolean(run) && run.code !== MISSING_NODES;
}

/** The pack reasons of dropped cards as one, each pack and node named once. */
function mergedPackReason(reasons) {
  if (!reasons.length) return null;
  const packs = new Map();
  for (const pack of reasons.flatMap((reason) => reason.packs)) {
    packs.set(pack.id, pack);
  }
  return {
    code: MISSING_NODES,
    nodes: [...new Set(reasons.flatMap((reason) => reason.nodes))],
    packs: [...packs.values()],
  };
}

/** Read the cards again, once the install's nodes have loaded. */
function reloadCards() {
  loaded.value = false;
  void loadCards();
}

/**
 * The card the tab falls back to: the first Image to Image card in the grid's
 * own order, else whatever edit card comes first - of the cards the open
 * picture alone can run. With none, the first a download would fix, so its
 * notice is on screen rather than behind the picker.
 */
function defaultKey() {
  const here = cards.value.filter((card) => runsHere(card.id));
  return (
    (here.find((card) => card.type === "img2img") || here[0])?.id ||
    missing.value.keys().next().value ||
    ""
  );
}

/** A listed card the open picture alone can run. */
function runsHere(key) {
  return (
    !fillInPopup.value.has(key) &&
    !missing.value.has(key) &&
    cards.value.some((card) => card.id === key)
  );
}

/**
 * True when the card runs on the open picture, `FILL` when the pre-flight's
 * only objection is a picture input the Run popup can fill, the
 * `missing_models` reason when missing models are its only objection, a
 * `missing_nodes` reason when its only objection is nodes a known pack
 * provides (the card is dropped, but the pack is offered), false otherwise.
 */
async function wouldRun(card) {
  try {
    return await verdict(card.id);
  } catch (err) {
    console.warn(`Could not pre-flight ${card.id} for the Edit tab:`, err);
    return true;
  }
}

/** `wouldRun`'s answer, throwing when the pre-flight itself fails. */
async function verdict(key) {
  const id = Number(props.pictureId);
  if (!Number.isFinite(id) || id <= 0) return true;
  const answer = await preflightWorkflowRun({
    picture_ids: [id],
    target: key,
  });
  const reasons = (answer?.groups || [])
    .flatMap((group) => group.reasons || [])
    .filter((reason) => !UNCHECKED_CODES.includes(reason.code));
  if (!reasons.length) return true;
  if (reasons.every((reason) => reason.code === PICTURE_INPUT_UNFILLED)) {
    return FILL;
  }
  // A model still downloading is a wait like a missing one, and the card
  // stays listed through it: one notice, the files on their way marked so.
  const waits = [MISSING_MODELS, MODELS_DOWNLOADING];
  if (reasons.every((reason) => waits.includes(reason.code))) {
    return {
      code: MISSING_MODELS,
      models: reasons.flatMap((reason) =>
        (reason.models || []).map((model) =>
          reason.code === MODELS_DOWNLOADING
            ? { ...model, downloading: true }
            : model,
        ),
      ),
    };
  }
  // Only when the packs cover every missing node: installing them must make
  // the card runnable, or offering the install here would be a false promise.
  if (reasons.every(packFixes)) {
    return {
      code: MISSING_NODES,
      nodes: reasons.flatMap((reason) => reason.nodes || []),
      packs: reasons.flatMap((reason) => reason.packs),
    };
  }
  return false;
}

/** A `missing_nodes` reason whose every node a known pack provides. */
function packFixes(reason) {
  if (reason.code !== MISSING_NODES || !reason.packs?.length) return false;
  const covered = new Set(reason.packs.flatMap((pack) => pack.nodes || []));
  return (reason.nodes || []).every((node) => covered.has(node));
}

watch(
  () => props.active && props.comfyuiConfigured,
  (active) => {
    if (active) void loadCards();
  },
  { immediate: true },
);

// A model just downloaded may be one a listed card is waiting for: ask those
// cards again, so Run turns on without a reload. Only the newest ask is kept,
// since two downloads can land a moment apart.
let recheckAsk = 0;
watch(() => tasksStore.downloadsLanded, recheckMissing);

async function recheckMissing() {
  const keys = [...missing.value.keys()];
  if (!keys.length) return;
  const ask = ++recheckAsk;
  // A failed re-check is no news: that card keeps waiting.
  const NO_ANSWER = {};
  const runs = await Promise.all(
    keys.map((key) =>
      verdict(key).catch((err) => {
        console.warn(`Could not pre-flight ${key} again for the Edit tab:`, err);
        return NO_ANSWER;
      }),
    ),
  );
  if (ask !== recheckAsk) return;
  const still = new Map(missing.value);
  const fill = new Set(fillInPopup.value);
  const dropped = new Set();
  const packs = packReason.value ? [packReason.value] : [];
  keys.forEach((key, index) => {
    const answer = runs[index];
    if (answer === NO_ANSWER) return;
    if (answer?.code === MISSING_MODELS) {
      still.set(key, answer);
      return;
    }
    still.delete(key);
    if (answer === FILL) fill.add(key);
    // Another reason now, which a fresh read would have dropped the card for.
    if (!listed(answer)) dropped.add(key);
    // A fresh read would also offer the pack that would bring it back.
    if (answer?.code === MISSING_NODES) packs.push(answer);
  });
  missing.value = still;
  fillInPopup.value = fill;
  // Only when the re-check found one: a notice mid-install keeps its reason.
  if (packs.length > (packReason.value ? 1 : 0)) {
    packReason.value = mergedPackReason(packs);
  }
  if (dropped.size) {
    cards.value = cards.value.filter((card) => !dropped.has(card.id));
    unrunnable.value += dropped.size;
    if (dropped.has(workflowId.value)) workflowId.value = defaultKey();
  }
}

/**
 * The picture this run made: the newest stack member that was not there
 * before the run. Null while the output has not been imported yet.
 */
async function findResult(current) {
  const { members } = await stackMemberIds(current.sourceId);
  const fresh = members.filter((row) => !current.beforeIds.has(String(row.id)));
  return selectNewestStackMember(fresh)?.id ?? null;
}

let resolveTimer = null;
// ponytail: polls the stack after ComfyUI finishes, because the output is
// imported a moment later; a `picture_imported` hook would be exact. Half a
// minute covers a slow import; *Show it* appears only once the poll finds it.
const RESOLVE_ATTEMPTS = 20;
const RESOLVE_DELAY_MS = 1500;

async function resolveResult(current, attempt = 1) {
  if (run.value !== current || !current.stack) return;
  // The popup could not read the stack before the run, so no member can be
  // told apart as this run's: say so rather than guess.
  if (!current.beforeIds) {
    run.value = { ...current, lookedInVain: true };
    return;
  }
  try {
    current.resultId = await findResult(current);
  } catch (err) {
    console.warn(
      `Could not look up the result of the edit on picture ${current.sourceId}:`,
      err,
    );
  }
  if (run.value !== current) return;
  run.value = { ...current };
  if (!current.resultId && attempt < RESOLVE_ATTEMPTS) {
    resolveTimer = setTimeout(
      () => resolveResult(run.value, attempt + 1),
      RESOLVE_DELAY_MS,
    );
  } else if (!current.resultId) {
    run.value = { ...current, lookedInVain: true };
  }
}

// The grid follows the runs started from it (`useGridComfyuiRuns`), so this
// tab reads that state rather than asking after its run itself.
watch(
  () => props.comfyuiProgress?.status,
  (status) => {
    const current = run.value;
    if (!current || current.status !== "running") return;
    if (status === "completed") {
      run.value = { ...current, status: "done" };
      void resolveResult(run.value);
    } else if (status === "failed") {
      run.value = {
        ...current,
        status: "failed",
        message: props.comfyuiProgress?.message || "ComfyUI could not run it.",
      };
    }
  },
);

// Whether the grid's ComfyUI state on screen is this tab's run: while it runs,
// and through the short "complete" tail after it, which the tab's own Last
// edit already says. The lightbox drops its own bar for as long as this
// holds. A later run from the grid's menus flips the state back to running,
// which this done run does not claim.
const tellsRun = computed(() => {
  const status = runHere.value?.status;
  if (status === "running") return true;
  return (
    status === "done" &&
    !!props.comfyuiProgress?.visible &&
    props.comfyuiProgress?.status === "completed"
  );
});

watch(tellsRun, (running) => emit("running", running));

onBeforeUnmount(() => {
  clearTimeout(resolveTimer);
  if (tellsRun.value) emit("running", false);
});

// Only offered once the result has been found, so it always has somewhere to go.
function showResult() {
  const current = run.value;
  if (current?.resultId) {
    emit("show-picture", current.resultId, current.sourceId);
  }
}

function onInstructionKeydown(event) {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    void submit();
  }
}

function openInWorkflows() {
  if (!workflowId.value) return;
  router.push({ name: "workflows", query: { workflow: workflowId.value } });
}

/**
 * The Run popup for this picture. *edit* is a card chosen because it needs
 * another picture: the popup opens as an edit of this one, reading none of its
 * recipe, as the tab's own Run would. *More options…* keeps the recipe read.
 */
function onMoreOptions(key = workflowId.value, edit = false) {
  const id = Number(props.pictureId);
  if (!Number.isFinite(id) || id <= 0) return;
  emit("more-options", {
    pictureId: id,
    workflowId: key,
    edit,
    // The tab's own choice, which the popup would otherwise re-default.
    stack: stack.value,
    // Trimmed as Run trims it, so a blank box means the same thing on both
    // paths: no instruction.
    prompt: instruction.value.trim(),
    // Where: the Paint selection, which the popup sends as the run's.
    selection: hasSelection.value ? props.paint.selection.value : undefined,
  });
}

async function submit() {
  const id = Number(props.pictureId);
  if (submitting.value || !workflowId.value || chosenMissing.value) return;
  if (!Number.isFinite(id) || id <= 0) return;
  submitting.value = true;
  submitError.value = "";
  clearTimeout(resolveTimer);
  const card = cards.value.find((row) => row.id === workflowId.value);
  const text = instruction.value.trim();
  try {
    // Before the run, so the member it adds can be told apart afterwards.
    const before = stack.value
      ? (await stackMemberIds(id)).ids
      : new Set([String(id)]);
    // Filed where the Run popup would file it: the set, project and person in
    // view, which the grid keeps on the store. Sent only when there is one.
    const view = runDialog.context || {};
    const destination = {
      set_id: view.set_id ?? null,
      project_id: view.project_id ?? null,
      character_id: view.character_id ?? null,
    };
    const pictureIds = runsOnAll.value ? props.applyToIds.map(Number) : [id];
    const answer = await runWorkflowCard({
      picture_ids: pictureIds,
      target: workflowId.value,
      // Where on the picture: rendered server-side into its MASK.
      ...(hasSelection.value ? { selection: props.paint.selection.value } : {}),
      // An empty box leaves the workflow's own prompt alone: `""` would blank
      // every positive prompt node in the graph.
      prompt: text || null,
      stack: stack.value,
      ...(Object.values(destination).some((value) => value != null)
        ? { destination }
        : {}),
      label: runTaskLabel({ workflow: card?.name }),
    });
    const prompts = Array.isArray(answer?.prompts) ? answer.prompts : [];
    if (!prompts.length) {
      const reasons = (answer?.groups || []).flatMap((g) => g.reasons || []);
      const said = reasons.map((reason) => readReason(reason).text);
      submitError.value = said.length
        ? `Nothing was queued: ${said.join(" ")}`
        : "Nothing was queued.";
      return;
    }
    remember(workflowId.value, hasSelection.value && usesMask.value);
    follow(id, card?.name, text, stack.value, before, pictureIds.length);
    // No picture ids: see the component's docstring.
    runDialog.started(prompts, []);
  } catch (err) {
    submitError.value = errorMessage(err, "Could not start the edit.");
  } finally {
    submitting.value = false;
  }
}

function follow(sourceId, workflowName, text, stacked, before, count = 1) {
  run.value = {
    count,
    sourceId,
    workflowName: workflowName || "Untitled workflow",
    instruction: text,
    stack: stacked,
    status: "running",
    message: "",
    beforeIds: before,
    resultId: null,
  };
}

// A run the Run popup started for this tab (*More options…*, or a card that
// needs a reference picture), followed as one of its own. App.vue has already
// handed it on without picture ids, so the lightbox stays put.
watch(
  () => runDialog.editRun,
  (started) => {
    if (!started) return;
    // Taken, so nothing stale is left on the store for a later reader.
    runDialog.editRun = null;
    clearTimeout(resolveTimer);
    // Read by the popup BEFORE it queued the run, as `submit` does here.
    follow(
      started.pictureId,
      started.workflowName,
      started.instruction,
      started.stack,
      started.beforeIds,
    );
  },
);

defineExpose({ submit });
</script>

<style scoped>
.edit-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.edit-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  /* Room for the focus ring, which is drawn outside a full-width field and was
     clipped to a top and bottom line by the scroll box. The negative margin
     keeps the fields' edges where they were. */
  --ring-room: calc(var(--focus-width) + var(--focus-offset));
  padding: var(--ring-room);
  margin: calc(-1 * var(--ring-room));
}

.edit-sec {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-5) 0 var(--space-3);
}

.edit-scroll > .edit-sec:first-child {
  padding-top: 0;
}

.edit-sec-end {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

/* The Recipe tab's `.recipe-sec-act`, in this tab's label row. */
.edit-sec-act {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  text-transform: none;
  letter-spacing: normal;
  font-weight: var(--weight-medium);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
}

.edit-sec-act:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-sec-act--help {
  flex-shrink: 0;
  white-space: nowrap;
}

/* Clear is what Escape does first, so it carries the selection's olive (the
   dark-surface one: the lightbox is dark in either theme). */
.edit-sec-act--clear {
  background: rgba(var(--v-theme-dark-surface-primary), 0.2);
  color: rgb(var(--v-theme-dark-surface-primary));
}

.edit-sec-act--clear:hover {
  background: rgba(var(--v-theme-dark-surface-primary), 0.32);
  color: rgb(var(--v-theme-dark-surface-primary));
}

.edit-kbd {
  font-family: inherit;
  font-size: var(--text-2xs);
  padding: 0 var(--space-1);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.15);
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-aside {
  text-transform: none;
  letter-spacing: normal;
  font-weight: var(--weight-medium);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.edit-select,
.edit-textarea {
  width: 100%;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.2);
  background: rgba(var(--v-theme-shadow), 0.35);
  color: rgb(var(--v-theme-on-dark-surface));
  font-family: inherit;
  font-size: var(--text-sm);
}

/* The field box, as a button so it opens the app's menu. */
.edit-select {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--control-h);
  padding: 0 var(--space-3);
  text-align: left;
  cursor: pointer;
}

.edit-select-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.edit-select-chevron {
  flex-shrink: 0;
  font-size: var(--gutter-glyph);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

/* The workflow's type, as the design's small chip on the field and each row. */
.edit-kind {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
  white-space: nowrap;
}

/* The missing-model flag, in the dark surface's warning hue. */
.edit-missing {
  display: inline-flex;
  flex-shrink: 0;
  color: rgb(var(--v-theme-dark-surface-warning));
}

/* RunReasonNotice in the on-dark skin, as AppButton's on-dark does it (its
   download button included). Under `.edit-pane` to outrank its own border. */
.edit-pane .edit-missing-notice {
  margin-top: var(--space-3);
  border-color: rgba(var(--v-theme-on-dark-surface), 0.2);
  border-left-color: rgb(var(--v-theme-dark-surface-error));
  color: rgb(var(--v-theme-on-dark-surface));
  --v-theme-on-surface: var(--v-theme-on-dark-surface);
  --v-theme-surface-error: var(--v-theme-dark-surface-error);
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);
}

/* At the top of the list or the empty state, so the space goes below it. */
.edit-pane .edit-pack-notice {
  margin-top: 0;
  margin-bottom: var(--space-4);
}

.edit-option[aria-checked="true"] {
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
}

.edit-menu-note {
  margin: 0;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
  white-space: normal;
}

.edit-textarea {
  display: block;
  min-height: 76px;
  padding: var(--space-3);
  line-height: var(--leading-snug);
  resize: vertical;
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-dark-surface), 0.4) transparent;
}

.edit-textarea::placeholder {
  color: rgba(var(--v-theme-on-dark-surface), 0.45);
}

.edit-help {
  margin-top: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.1);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.edit-help p {
  margin: 0;
}

.edit-help p + p {
  margin-top: var(--space-2);
}

.edit-note--lead {
  margin-top: 0;
}

/* Grow, feather, fill holes: label, slider, value (mono). */
.edit-slider {
  display: grid;
  grid-template-columns: 64px 1fr 52px;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.edit-slider input {
  width: 100%;
  min-width: 0;
  accent-color: rgb(var(--v-theme-primary));
}

.edit-slider output {
  text-align: right;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-pane :deep(.seg) {
  --v-theme-on-surface: var(--v-theme-on-dark-surface);
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);
  --track-trough: rgba(var(--v-theme-scrim), 0.34);
  --track-ring: rgba(var(--v-theme-on-dark-surface), 0.4);
}

.edit-check {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-check input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
}

.edit-bar {
  height: 4px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-dark-surface), 0.12);
  overflow: hidden;
}

.edit-bar i {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-dark-surface-accent));
  transition: width var(--dur-2) var(--ease-standard);
}

.edit-note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.edit-empty-lead {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-empty .edit-note {
  margin-bottom: var(--space-4);
}

.edit-last {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
}

.edit-last-thumb {
  width: 48px;
  height: 32px;
  flex: none;
  object-fit: cover;
  border-radius: var(--radius-sm);
}

.edit-last-text {
  flex: 1;
  min-width: 0;
}

.edit-last-text .edit-note {
  margin-top: var(--space-1);
}

.edit-last-instruction {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-dark-surface));
  overflow-wrap: anywhere;
}

.edit-link {
  padding: 0;
  font-family: inherit;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.edit-link:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}

.edit-foot {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  margin-top: var(--space-4);
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
}

.edit-foot .edit-note {
  margin: 0;
}

.edit-foot-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.edit-undo {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
