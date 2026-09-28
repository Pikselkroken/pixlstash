<template>
  <AppDialog
    :open="open"
    title="Create with LoRA"
    :subtitle="entityName"
    :persistent="submitting"
    @close="onRequestClose"
    @accept="submit"
  >
    <p v-if="loadFailed" class="cwl-note cwl-note--bad" role="alert">
      {{ loadFailed }}
    </p>
    <p v-else-if="loading" class="cwl-note" role="status">
      Reading {{ entityName }}'s LoRAs and your workflows…
    </p>
    <p v-else-if="!loras.length" class="cwl-note" role="status">
      No LoRA is attached to {{ entityName }}. Assign one from the Models shelf,
      then come back here.
    </p>

    <template v-else>
      <div v-if="loras.length > 1" class="cwl-field">
        <span class="cwl-l">LoRA</span>
        <OptionRows
          v-model="loraSha"
          :options="loraOptions"
          aria-label="LoRA"
          :disabled="submitting"
        />
      </div>
      <p v-else class="cwl-lede">
        With <b>{{ loraLabel(lora) }}</b>
        <span v-if="lora?.base_model" class="cwl-sub">
          · {{ lora.base_model }}</span
        >
      </p>

      <div class="cwl-field">
        <span class="cwl-l">Workflow</span>
        <OptionRows
          v-if="fits.ready.length"
          v-model="workflowId"
          :options="workflowOptions"
          aria-label="Workflow"
          :disabled="submitting"
        >
          <template #meta="{ option }">
            <span class="cwl-sub cwl-meta">{{ option.meta }}</span>
          </template>
        </OptionRows>
        <p v-else class="cwl-note" role="status">
          None of your workflows has a LoRA loader on a checkpoint this LoRA
          fits.
        </p>
        <!-- Said, not hidden without a word: a workflow the owner expected
             here and cannot find needs a reason. -->
        <p v-if="leftOut" class="cwl-note">{{ leftOut }}</p>
      </div>

      <p v-if="slotNote" class="cwl-note" role="status">{{ slotNote }}</p>

      <div class="cwl-field">
        <span class="cwl-l">Prompt</span>
        <AppTextarea
          v-model="prompt"
          :rows="3"
          placeholder="The workflow's own prompt"
          :disabled="submitting"
          @keydown.stop
        />
        <p v-if="lora?.trigger_words" class="cwl-note cwl-trigger">
          Trigger words: {{ lora.trigger_words }}
          <AppButton
            size="sm"
            variant="ghost"
            :disabled="submitting || promptHasTriggers"
            @click="addTriggers"
            >Add to prompt</AppButton
          >
        </p>
      </div>

      <div class="cwl-controls">
        <div class="cwl-field">
          <span class="cwl-l">Strength</span>
          <AppInput
            v-model.number="strength"
            aria-label="Strength"
            type="number"
            step="0.05"
            min="-10"
            max="10"
            :disabled="submitting"
            @keydown.stop
          />
        </div>
        <div class="cwl-field">
          <span class="cwl-l">Count</span>
          <AppInput
            v-model.number="count"
            aria-label="Count"
            type="number"
            min="1"
            :max="String(MAX_RUNS)"
            :disabled="submitting"
            @keydown.stop
          />
        </div>
      </div>
      <p class="cwl-note">Results go to {{ entityName }}.</p>

      <div v-if="reasons.length" class="cwl-reasons" role="alert">
        <RunReasonNotice
          v-for="reason in reasons"
          :key="reason.code"
          :reason="reason"
          :busy="checking"
          @settings="emit('open-settings', 'compute')"
          @retry="resolveSlot"
        />
      </div>
      <p v-if="submitError" class="cwl-note cwl-note--bad" role="alert">
        {{ submitError }}
      </p>
    </template>

    <template #footer>
      <p v-if="blocker" :id="blockerId" class="cwl-note cwl-note--bad">
        {{ blocker }}
      </p>
      <span class="cwl-sp" />
      <AppButton :disabled="submitting" @click="onRequestClose"
        >Cancel</AppButton
      >
      <!-- `aria-disabled`, not `disabled`, so the reason stays reachable by
           keyboard; `submit()` does the refusing. -->
      <AppButton
        variant="primary"
        icon-left="play"
        :loading="submitting"
        :class="{ 'run-refused': !canRun }"
        :aria-disabled="canRun ? undefined : 'true'"
        :aria-describedby="blocker ? blockerId : undefined"
        @click="submit"
      >
        {{ count > 1 ? `Run ${count}` : "Run" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * "Create with LoRA…" from a person's or a picture set's context menu.
 *
 * Lists the workflows that can run the LoRA attached to that person or set,
 * with the LoRA already in the workflow's loader, and files the results to
 * the person or set. Which workflows fit is worked out here from the cards,
 * the shelf row and the hand-made workflow sets (`utils/loraWorkflows.js`);
 * the slot the LoRA goes into comes from the chosen workflow's chain read.
 * The run is the ordinary `POST /workflows/run` with `workflow_id`, one
 * addressed `loras` entry and a `destination`.
 */
import { computed, ref, useId, watch } from "vue";

import { fetchWorkflowSets, listAdapters } from "../../api/modelShelf";
import {
  getLoraChain,
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
} from "../../api/workflows";
import { errorMessage } from "../../utils/apiError";
import { fitWorkflows, pickLoraSlot } from "../../utils/loraWorkflows";
import { modelName } from "../../utils/modelShelf";
import { modelDisplayName } from "../../utils/workflowCard";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import OptionRows from "../widgets/OptionRows.vue";
import RunReasonNotice from "./RunReasonNotice.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `{entityType: "character" | "set", entityId, name}`. */
  source: { type: Object, default: null },
  /** `{client_id}` from the grid, when one is mounted. */
  context: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["close", "run", "open-settings"]);

/** `MAX_RUNS_PER_REQUEST` (`pixlstash/routes/comfyui.py`). */
const MAX_RUNS = 200;
/** Both file kinds an attachment can be read back through (`AdapterTray`). */
const ATTACHABLE_FILE_KINDS = ["adapter", "unknown"];
const FILTER_KEY = { character: "characterId", set: "setId" };

const blockerId = useId();
const loading = ref(false);
const loadFailed = ref("");
const checking = ref(false);
const submitting = ref(false);
const submitError = ref("");
const loras = ref([]);
const cards = ref([]);
const handMade = ref([]);
const loraSha = ref(null);
const workflowId = ref(null);
/** `{loader, replaces}` for the chosen workflow, or null. */
const slot = ref(null);
const slotError = ref("");
const reasons = ref([]);
const prompt = ref("");
const strength = ref(1);
const count = ref(1);

const entityName = computed(() => props.source?.name || "");
const lora = computed(
  () => loras.value.find((row) => row.sha256 === loraSha.value) || null,
);
const fits = computed(() =>
  fitWorkflows(cards.value, lora.value, handMade.value),
);

const loraOptions = computed(() =>
  loras.value.map((row) => ({ id: row.sha256, label: loraLabel(row) })),
);
const workflowOptions = computed(() =>
  fits.value.ready.map((entry) => ({
    id: entry.card.id,
    label: entry.card.name,
    meta: metaOf(entry),
  })),
);

const leftOut = computed(() => {
  const parts = [];
  const { clash, noLoader } = fits.value;
  if (clash.length) {
    parts.push(
      `${clash.length} ${clash.length === 1 ? "is" : "are"} for a different base model`,
    );
  }
  if (noLoader.length) {
    parts.push(
      `${noLoader.length} ${noLoader.length === 1 ? "has" : "have"} no LoRA loader`,
    );
  }
  if (!parts.length) return "";
  const total = clash.length + noLoader.length;
  return `${total} more ${total === 1 ? "workflow is" : "workflows are"} not listed: ${parts.join(", ")}.`;
});

const slotNote = computed(() => {
  if (slotError.value) return slotError.value;
  if (slot.value?.replaces)
    return `Replaces ${slot.value.replaces} in this run.`;
  return "";
});

const validCount = computed(
  () =>
    Number.isInteger(count.value) && count.value > 0 && count.value <= MAX_RUNS,
);
const validStrength = computed(
  () => Number.isFinite(strength.value) && Math.abs(strength.value) <= 10,
);
const promptHasTriggers = computed(
  () =>
    Boolean(lora.value?.trigger_words) &&
    prompt.value.includes(lora.value.trigger_words),
);

const blocker = computed(() => {
  if (loading.value || loadFailed.value || !loras.value.length) return "";
  if (!workflowId.value) return "Pick a workflow.";
  if (checking.value) return "";
  if (!slot.value)
    return slotError.value ? "This workflow cannot take the LoRA." : "";
  if (!validCount.value)
    return `How many? A whole number from 1 to ${MAX_RUNS}.`;
  if (!validStrength.value) return "Strength is a number from -10 to 10.";
  if (reasons.value.length)
    return "This workflow cannot run; see the reason above.";
  return "";
});

const canRun = computed(
  () =>
    !submitting.value &&
    !loading.value &&
    !checking.value &&
    Boolean(slot.value) &&
    !blocker.value,
);

function loraLabel(row) {
  return row ? modelName(row) : "";
}

function metaOf(entry) {
  const parts = [];
  const checkpoint = modelDisplayName(entry.checkpoint);
  if (checkpoint) parts.push(checkpoint);
  if (entry.paired) parts.push("in a workflow set with this LoRA");
  else if (entry.usedBefore) parts.push("used this LoRA before");
  return parts.join(" · ");
}

function addTriggers() {
  const words = lora.value?.trigger_words;
  if (!words || promptHasTriggers.value) return;
  prompt.value = prompt.value.trim()
    ? `${words}, ${prompt.value.trim()}`
    : words;
}

function runBody() {
  const loader = slot.value?.loader;
  const destination =
    props.source?.entityType === "character"
      ? { character_id: Number(props.source.entityId) }
      : { set_id: Number(props.source?.entityId) };
  return {
    workflow_id: workflowId.value,
    // `null` leaves the workflow's own prompt; only typed text replaces it.
    prompt: prompt.value.trim() ? prompt.value : null,
    loras: loader
      ? [
          {
            node_id: loader.node_id,
            field: loader.field,
            sha256: lora.value.sha256,
            strength_model: strength.value,
          },
        ]
      : [],
    count: count.value,
    seed_mode: "new",
    client_id: props.context?.client_id || null,
    destination,
  };
}

/** Bumped per read, so a slower earlier answer cannot write over a later one. */
let loadToken = 0;
let slotToken = 0;

async function load() {
  const token = (loadToken += 1);
  const mine = () => token === loadToken;
  const filterKey = FILTER_KEY[props.source?.entityType];
  const entityId = Number(props.source?.entityId);
  loading.value = true;
  loadFailed.value = "";
  loras.value = [];
  if (!filterKey || !Number.isInteger(entityId)) {
    loading.value = false;
    loadFailed.value = "Nothing to make pictures of.";
    return;
  }
  try {
    const [attached, library, sets] = await Promise.all([
      Promise.all(
        ATTACHABLE_FILE_KINDS.map((fileKind) =>
          listAdapters({ fileKind, [filterKey]: entityId }),
        ),
      ),
      listWorkflowCards(),
      // A set only ranks the list; without it every workflow still shows.
      fetchWorkflowSets().catch((err) => {
        console.warn("Workflow sets unavailable; ranking without them.", err);
        return { hand_made: [] };
      }),
    ]);
    if (!mine()) return;
    loras.value = attached.flat().filter((row) => row.sha256);
    cards.value = library?.cards || [];
    handMade.value = sets?.hand_made || [];
    loraSha.value = loras.value[0]?.sha256 || null;
  } catch (err) {
    if (mine()) {
      loadFailed.value = errorMessage(
        err,
        "Could not read the LoRAs and workflows.",
      );
    }
  } finally {
    if (mine()) loading.value = false;
  }
}

/** Find the chosen workflow's loader for this LoRA, then pre-flight the run. */
async function resolveSlot() {
  const token = (slotToken += 1);
  const mine = () => token === slotToken;
  slot.value = null;
  slotError.value = "";
  reasons.value = [];
  submitError.value = "";
  if (!workflowId.value || !lora.value) return;
  checking.value = true;
  try {
    const chain = await getLoraChain(workflowId.value);
    if (!mine()) return;
    const picked = pickLoraSlot(chain, lora.value);
    if (!picked) {
      slotError.value = "Could not find a LoRA loader in this workflow.";
      return;
    }
    slot.value = picked;
    const answer = await preflightWorkflowRun(runBody());
    if (!mine()) return;
    reasons.value = (answer?.groups || []).flatMap(
      (group) => group.reasons || [],
    );
  } catch (err) {
    if (mine()) {
      slotError.value = errorMessage(
        err,
        "Could not read this workflow's LoRA loaders.",
      );
    }
  } finally {
    if (mine()) checking.value = false;
  }
}

function onRequestClose() {
  if (submitting.value) return;
  emit("close");
}

async function submit() {
  if (!canRun.value) return;
  submitting.value = true;
  submitError.value = "";
  try {
    const answer = await runWorkflowCard(runBody());
    const prompts = Array.isArray(answer?.prompts) ? answer.prompts : [];
    if (!prompts.length) {
      reasons.value = (answer?.groups || []).flatMap(
        (group) => group.reasons || [],
      );
      submitError.value = "Nothing was queued; see the reason above.";
      return;
    }
    emit("run", { prompts, pictureIds: [] });
    emit("close");
  } catch (err) {
    submitError.value = errorMessage(err, "Could not start the run.");
  } finally {
    submitting.value = false;
  }
}

// A new LoRA can change which workflows fit: keep the choice only if it still
// does, else take the best one.
watch(fits, (next) => {
  const ids = next.ready.map((entry) => entry.card.id);
  if (!ids.includes(workflowId.value)) workflowId.value = ids[0] || null;
});

watch([workflowId, loraSha], () => {
  void resolveSlot();
});

watch(
  () => [props.open, props.source],
  ([open]) => {
    if (!open) return;
    prompt.value = "";
    strength.value = 1;
    count.value = 1;
    workflowId.value = null;
    void load();
  },
  { immediate: true },
);
</script>

<style scoped>
:deep(.run-refused) {
  opacity: var(--opacity-disabled);
}

.cwl-lede {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.cwl-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cwl-controls {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.cwl-sub,
.cwl-l,
.cwl-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cwl-meta {
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cwl-note {
  margin: 0;
  line-height: var(--leading-body);
}

.cwl-note--bad {
  color: rgb(var(--v-theme-surface-error));
}

.cwl-trigger {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.cwl-reasons {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cwl-sp {
  flex: 1;
}
</style>
