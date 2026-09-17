<template>
  <AppInspector
    v-model="tab"
    class="wfrun"
    label="Run a workflow"
    :open="sidebarStore.statsOpen"
    :tabs="TABS"
  >
    <div class="inspector-section">
      <div class="wfrun-head">
        <span class="section-label">Workflow</span>
        <AppBarButton
          icon="close"
          tooltip="Close the run panel"
          @click="runStore.close()"
        />
      </div>
      <p v-if="listLoading && !workflows.length" class="wfrun-note">
        Reading your workflows…
      </p>
      <p v-else-if="listError" class="wfrun-note wfrun-error">
        {{ listError }}
      </p>
      <p v-else-if="!offered.length" class="wfrun-note">{{ noneOffered }}</p>
      <AppSelect
        v-else
        v-model="chosenName"
        label="Workflow"
        hide-label
        :options="
          offered.map((w) => ({
            value: w.name,
            label: w.display_name || w.name,
          }))
        "
      />
    </div>

    <template v-if="chosen">
      <div v-if="inputs === null || inputs.length" class="inspector-section">
        <span class="section-label">Pictures in</span>
        <p v-if="inputsError" class="wfrun-note wfrun-error">
          {{ inputsError }}
        </p>
        <p v-else-if="inputs === null" class="wfrun-note">
          Reading its inputs…
        </p>
        <div
          v-for="input in inputs || []"
          v-else
          :key="input.node_id"
          class="wfrun-input"
        >
          <!-- The node id beside the title, as in Workflows: two inputs are
               often both "Load Image". -->
          <div class="wfrun-input-head">
            <span class="wfrun-input-name">{{ input.title }}</span>
            <span class="wfrun-mono">#{{ input.node_id }}</span>
          </div>
          <p v-if="input.mode === 'selection'" class="wfrun-note">
            {{ selectionLine }}
          </p>
          <div v-else class="wfrun-picture">
            <img
              v-if="pictureOf(input)"
              class="wfrun-thumb"
              :src="thumbUrl(pictureOf(input))"
              :alt="`The picture for ${input.title} #${input.node_id}`"
            />
            <span v-else class="wfrun-thumb wfrun-thumb--empty">
              <v-icon size="18">mdi-image-off-outline</v-icon>
            </span>
            <span class="wfrun-note wfrun-picture-text">{{
              pictureLine(input)
            }}</span>
            <!-- Fixed is chosen in Workflows and only shown here. -->
            <AppButton
              v-if="input.mode === 'picker'"
              size="sm"
              :aria-label="`Choose the picture for ${input.title} #${input.node_id}`"
              @click="pickerFor = input"
            >
              {{ picks[input.node_id] ? "Change" : "Choose" }}
            </AppButton>
          </div>
        </div>
      </div>

      <div v-if="takesPrompt" class="inspector-section">
        <span class="section-label">Prompt</span>
        <AppTextarea
          v-model="caption"
          :rows="4"
          placeholder="Leave empty to keep the workflow's own prompt"
          @keydown.stop
        />
      </div>

      <!-- The LoRA goes into a loader the workflow already has (#1310), or
           into one PixlStash adds where it has none and can show where
           (#1376); otherwise it says why not. Shown only once the inputs are
           read: before that "no LoRA loader" would be a guess, and after a
           failed read a confident wrong one. -->
      <div v-if="inputs !== null && !inputsError" class="inspector-section">
        <span class="section-label">LoRA</span>
        <!-- role=status on the note itself, not the section: a live region
             around the selects would announce every option change. -->
        <p v-if="!loraSlots.length && !canInsert" class="wfrun-note" role="status">
          {{ noLoraText("This workflow") }}
        </p>
        <p v-else-if="adaptersError" class="wfrun-note wfrun-error" role="alert">
          {{ adaptersError }}
        </p>
        <template v-else>
          <AppSelect
            v-model="adapterSha"
            label="LoRA"
            hide-label
            :options="adapterOptions"
          />
          <!-- What adding the loader does, before the run does it. -->
          <p v-if="canInsert && adapterSha" class="wfrun-note" role="status">
            {{ insertionText }}
          </p>
          <!-- Which slot, when the workflow has more than one: swapping them
               all would load the chosen LoRA twice and lose the others. -->
          <AppSelect
            v-if="loraSlots.length > 1"
            v-model="chosenSlot"
            label="Into which loader"
            :options="slotOptions"
          />
        </template>
      </div>

      <div class="inspector-section">
        <span class="section-label">Seed</span>
        <Segmented
          v-model="seedMode"
          :options="SEED_MODE_OPTIONS"
          variant="icon-label"
          full
          aria-label="Seed mode"
        />
        <AppInput
          v-if="seedMode === 'fixed'"
          v-model.number="seed"
          type="number"
          aria-label="Seed"
          min="0"
          max="4294967295"
          @keydown.stop
        />
        <label v-if="fromSelection" class="wfrun-check">
          <input v-model="stackOutputs" type="checkbox" />
          <span>Stack new pictures with the ones they came from</span>
        </label>
      </div>

      <div class="inspector-section">
        <!-- The count is stated before anything starts: a selection of 40
             is 40 ComfyUI runs, and the button is where that is decided. -->
        <p class="wfrun-note" :class="{ 'wfrun-error': blocker }">
          {{ blocker || countLine }}
        </p>
        <AppButton
          variant="primary"
          icon-left="play"
          block
          :disabled="Boolean(blocker)"
          :loading="running"
          @click="run"
        >
          {{ runLabel }}
        </AppButton>
        <p v-if="runError" class="wfrun-note wfrun-error" role="alert">
          {{ runError }}
        </p>
        <p v-else-if="runDone" class="wfrun-note" role="status">
          {{ runDone }}
        </p>
      </div>
    </template>

    <PicturePicker
      :open="pickerFor !== null"
      :subtitle="
        pickerFor ? `for ${pickerFor.title} #${pickerFor.node_id}` : ''
      "
      @close="pickerFor = null"
      @pick="onPicked"
    />
  </AppInspector>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import {
  getLoraInsertion,
  getWorkflowInputs,
  listWorkflows,
  runWorkflow,
} from "../../api/comfyui";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useLoraSwap } from "../../composables/useLoraSwap";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { SCRAPHEAP_PICTURES_ID } from "../../stores/useViewStore";
import { isReadOnly } from "../../utils/apiClient";
import { useGenStackPrefsStore } from "../../stores/useGenStackPrefsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import {
  FROM_SELECTION,
  useWorkflowRunStore,
} from "../../stores/useWorkflowRunStore";
import { errorDetail } from "../../utils/apiError";
import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppInspector from "../widgets/AppInspector.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
import Segmented from "../widgets/Segmented.vue";

const TABS = [{ value: "run", label: "Run", icon: "mdi-play-outline" }];
const SEED_MODE_OPTIONS = [
  { id: "random", label: "Random", icon: "dice-multiple-outline" },
  { id: "fixed", label: "Fixed", icon: "lock-outline" },
];
const MAX_SEED = 4294967295;

const runStore = useWorkflowRunStore();
const sidebarStore = useSidebarStore();
const genStackPrefs = useGenStackPrefsStore();
const selectionStore = useSelectionStore();

const tab = ref("run");
const workflows = ref([]);
const listLoading = ref(false);
const listError = ref("");
/** The chosen workflow per origin, so switching origin keeps each choice. */
const chosenByOrigin = reactive({});
/** `null` while its inputs are read. */
const inputs = ref(null);
const inputsError = ref("");
/** Picker input node id -> the picture chosen for it. */
const picks = reactive({});
const pickerFor = ref(null);
const chosenName = computed({
  get: () => chosenByOrigin[runStore.origin] || "",
  set: (name) => {
    chosenByOrigin[runStore.origin] = name;
  },
});

/** Every LoRA slot of the chosen workflow, as the inputs route reports them. */
const loraSlots = ref([]);

/**
 * Where a loader would go, asked once the inputs say the workflow has none.
 * Declared after `chosenName` rather than relying on a short-circuit: the swap
 * reads this during setup, so anything it names has to exist by now.
 */
const loraInsertionSource = computed(() => {
  if (inputs.value === null || inputsError.value) return null;
  const name = chosenName.value;
  return name ? { key: name, load: () => getLoraInsertion(name) } : null;
});
const {
  adapterSha,
  chosenSlot,
  adaptersError,
  adapterOptions,
  slotOptions,
  canInsert,
  insertionText,
  noLoaderText: noLoraText,
  body: loraBody,
} = useLoraSwap(loraSlots, loraInsertionSource);
const caption = ref("");
const seedMode = ref("random");
const seed = ref(0);
const running = ref(false);
const runError = ref("");
const runDone = ref("");

const fromSelection = computed(() => runStore.origin === FROM_SELECTION);

const stackOutputs = computed({
  get: () => genStackPrefs.stackI2IOutputs,
  set: (value) => genStackPrefs.setStackI2IOutputs(value),
});

// The pill offers what a selection fills and the toolbar what needs none, so
// a workflow is never offered where it would refuse to run. An absent field
// reads as the backend's own default.
const offered = computed(() =>
  workflows.value.filter(
    (w) =>
      w?.runnable !== false &&
      (w?.has_selection_input !== false) === fromSelection.value,
  ),
);

const noneOffered = computed(() =>
  fromSelection.value
    ? "No workflow runs on a selection. It needs a save node, API format, and a picture input set to Selection in Workflows."
    : "No workflow runs without a selection. One whose picture inputs are all Picker or Fixed in Workflows is offered here.",
);

const chosen = computed(
  () => offered.value.find((w) => w.name === chosenName.value) || null,
);

const takesPrompt = computed(
  () => !chosen.value?.missing_placeholders?.includes("{{caption}}"),
);

const selectionCount = computed(() => runStore.selectionIds.length);

const runCount = computed(() =>
  fromSelection.value ? selectionCount.value : 1,
);

const selectionLine = computed(() =>
  selectionCount.value
    ? `Filled by the selection: ${selectionCount.value} ${
        selectionCount.value === 1 ? "picture" : "pictures"
      }.`
    : "Filled by the selection. Nothing is selected.",
);

const runLabel = computed(() =>
  runCount.value === 1 ? "Run once" : `Run ${runCount.value} times`,
);

const countLine = computed(() =>
  fromSelection.value
    ? `One ComfyUI run for each selected picture: ${runCount.value} in all.`
    : "One ComfyUI run.",
);

/** Why the run cannot start, or "" when it can. */
const blocker = computed(() => {
  if (isReadOnly.value) return "This session is read-only.";
  if (!chosen.value) return "Choose a workflow.";
  if (inputsError.value || inputs.value === null)
    return "Its inputs are not read yet.";
  if (fromSelection.value && !selectionCount.value)
    return "Select the pictures to run it on.";
  if (
    fromSelection.value &&
    String(selectionStore.selectedCharacter).toUpperCase() ===
      String(SCRAPHEAP_PICTURES_ID).toUpperCase()
  )
    return "Pictures in the Scrapheap cannot be run on.";
  const missing = inputs.value.find(
    (input) => input.mode === "fixed" && input.picture_missing,
  );
  if (missing)
    return `The fixed picture of ${missing.title} #${missing.node_id} has left the library. Choose another in Workflows.`;
  const unpicked = inputs.value.find(
    (input) => input.mode === "picker" && !picks[input.node_id],
  );
  if (unpicked)
    return `Choose a picture for ${unpicked.title} #${unpicked.node_id}.`;
  if (
    seedMode.value === "fixed" &&
    !(Number.isInteger(seed.value) && seed.value >= 0 && seed.value <= MAX_SEED)
  )
    return `The seed must be a whole number from 0 to ${MAX_SEED}.`;
  return "";
});

function pictureOf(input) {
  return input.mode === "picker"
    ? picks[input.node_id]?.id || null
    : input.picture_id || null;
}

function pictureLine(input) {
  if (input.mode === "picker")
    return picks[input.node_id] ? "Chosen for this run." : "Not chosen yet.";
  return input.picture_missing
    ? "Fixed, and no longer in this library."
    : "Fixed in Workflows.";
}

function thumbUrl(id) {
  return pictureThumbnailUrl(id);
}

function onPicked(picture) {
  if (pickerFor.value) picks[pickerFor.value.node_id] = picture;
  pickerFor.value = null;
}

async function loadWorkflows() {
  listLoading.value = true;
  listError.value = "";
  try {
    const body = await listWorkflows();
    workflows.value = Array.isArray(body?.workflows) ? body.workflows : [];
  } catch (err) {
    listError.value = errorDetail(err) || err?.message || String(err);
  } finally {
    listLoading.value = false;
  }
}

// A request for a workflow that is no longer chosen must not land.
let inputsRequest = 0;
async function loadInputs(name) {
  const request = ++inputsRequest;
  inputs.value = null;
  inputsError.value = "";
  loraSlots.value = [];
  for (const key of Object.keys(picks)) delete picks[key];
  if (!name) return;
  try {
    const body = await getWorkflowInputs(name);
    if (request !== inputsRequest) return;
    inputs.value = body?.inputs || [];
    loraSlots.value = body?.lora_slots || [];
  } catch (err) {
    if (request === inputsRequest)
      inputsError.value = errorDetail(err) || err?.message || String(err);
  }
}

async function run() {
  if (blocker.value || running.value) return;
  running.value = true;
  runError.value = "";
  runDone.value = "";
  const { client_id, ...viewContext } = runStore.context || {};
  const body = {
    pictures: Object.entries(picks).map(([node_id, picture]) => ({
      node_id,
      picture_id: picture.id,
    })),
    caption: caption.value || "",
    ...loraBody(),
    seed_mode: seedMode.value,
    seed: seedMode.value === "fixed" ? seed.value : undefined,
    client_id: client_id || undefined,
  };
  if (fromSelection.value) {
    body.picture_ids = [...runStore.selectionIds];
    body.stack = stackOutputs.value;
  } else {
    Object.assign(body, viewContext);
  }
  try {
    const response = await runWorkflow(chosen.value.name, body);
    const prompts = Array.isArray(response?.prompts) ? response.prompts : [];
    runStore.started(prompts);
    const started =
      prompts.length === 1
        ? "Started 1 run in ComfyUI."
        : `Started ${prompts.length} runs in ComfyUI.`;
    // A batch ComfyUI stopped partway: the runs it started still import.
    if (response?.status === "partial")
      runError.value = `${started} The rest did not start: ${response.error}`;
    else runDone.value = started;
  } catch (err) {
    runError.value = errorDetail(err) || err?.message || String(err);
  } finally {
    running.value = false;
  }
}

// Re-read whenever the panel opens or changes origin: a workflow can be added
// or set up in Workflows between two runs.
watch(
  () => [runStore.open, runStore.origin],
  ([open]) => {
    runError.value = "";
    runDone.value = "";
    if (open) loadWorkflows();
  },
  { immediate: true },
);

watch(offered, (list) => {
  if (list.some((w) => w.name === chosenName.value)) return;
  chosenName.value = list.length ? list[0].name : "";
});

watch(
  () => chosen.value?.name,
  (name) => {
    runError.value = "";
    runDone.value = "";
    loadInputs(name);
  },
  { immediate: true },
);
</script>

<style scoped>
.wfrun-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.wfrun-input {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.wfrun-input + .wfrun-input {
  padding-top: var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.wfrun-input-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
}

.wfrun-input-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfrun-mono {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfrun-picture {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.wfrun-thumb {
  width: var(--space-8);
  height: var(--space-8);
  flex: none;
  border-radius: var(--radius-sm);
  object-fit: cover;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.wfrun-thumb--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfrun-picture-text {
  flex: 1;
  min-width: 0;
}

.wfrun-check {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.wfrun-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfrun-error {
  color: rgb(var(--v-theme-surface-error));
}
</style>
