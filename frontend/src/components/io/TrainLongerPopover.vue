<template>
  <div
    class="tbm tlp"
    role="dialog"
    :aria-label="title"
    data-testid="train-longer"
    @keydown.esc.stop="emit('close')"
  >
    <div class="tlp-title">{{ title }}</div>

    <div v-if="!options" class="tlp-note" role="status">
      <v-icon size="16" class="mdi-spin">mdi-loading</v-icon>
      <span>Reading the steps it kept…</span>
    </div>
    <p v-else-if="options.reason" class="tlp-note" data-testid="tlp-reason">
      {{ options.reason }}
    </p>

    <template v-else>
      <div class="tlp-field">
        <FieldLabel>From</FieldLabel>
        <OptionRows
          v-model="fromStep"
          :options="stepOptions"
          aria-label="Train on from"
        >
          <template #meta="{ option }">
            <span v-if="option.keepsOptimizer" class="tlp-meta"
              >keeps the optimizer</span
            >
          </template>
        </OptionRows>
      </div>

      <div class="tlp-field">
        <FieldLabel>More steps</FieldLabel>
        <div class="tlp-steps">
          <input
            class="tlp-range"
            type="range"
            :min="SLIDER_MIN"
            :max="SLIDER_MAX"
            :step="snap"
            :value="Math.min(Math.max(more, SLIDER_MIN), SLIDER_MAX)"
            aria-label="More steps"
            :aria-valuetext="`${more.toLocaleString()} more steps`"
            data-testid="tlp-slider"
            @input="more = Number($event.target.value)"
          />
          <AppInput
            class="tlp-number"
            type="number"
            min="1"
            mono
            :model-value="more"
            aria-label="More steps"
            data-testid="tlp-number"
            @update:model-value="typed"
            @enter="start"
          />
        </div>
        <div class="tlp-scale" aria-hidden="true">
          <span>+{{ SLIDER_MIN.toLocaleString() }}</span>
          <span>+{{ SLIDER_MAX.toLocaleString() }}</span>
        </div>
        <span class="tlp-hint" data-testid="tlp-ends">{{ endsAt }}</span>
      </div>

      <p class="tlp-hint tlp-trade">
        The same run grows, and its new checkpoints join this stack on the
        shelf. Workflows that load the final file get the longer-trained one.
      </p>
    </template>

    <div class="tlp-acts">
      <AppButton size="md" variant="secondary" @click="emit('close')"
        >Cancel</AppButton
      >
      <AppButton
        size="md"
        variant="primary"
        icon-left="play"
        :loading="starting"
        :disabled="!canStart"
        data-testid="tlp-start"
        @click="start"
        >Start</AppButton
      >
    </div>
  </div>
</template>

<script setup>
// Train longer (owner ruling 2026-11-04, "Train longer, same run"): the same
// run trains on from a step it kept, in the same folder, with the same
// pictures and settings, and its new checkpoints join the same shelf stack.
// Two decisions, both small, so a popover rather than a dialog: which kept
// step (the newest preselected, the only one that keeps the optimizer) and
// how many more steps. The run bar, the grid card's menu and the selection
// pill all open this same panel; the host owns the anchor.

import { computed, onMounted, ref } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import OptionRows from "../widgets/OptionRows.vue";
import { getTrainLongerOptions, trainJobLonger } from "../../api/training";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  jobId: { type: Number, required: true },
  title: { type: String, default: "Train longer" },
});
const emit = defineEmits(["close", "started"]);

// The slider covers the recipes' range (the Strong recipe's ceiling) and snaps
// to the run's save interval, so a dragged end lands on a checkpoint. The
// field takes any whole number past it.
const SLIDER_MIN = 250;
const SLIDER_MAX = 3000;
const FINISHED_MORE = 1000;

const options = ref(null);
const fromStep = ref(null);
const more = ref(FINISHED_MORE);
const starting = ref(false);

const snap = computed(() => options.value?.save_every || SLIDER_MIN);

const stepOptions = computed(() =>
  (options.value?.steps ?? []).map((entry) => ({
    id: entry.step,
    label: `Step ${entry.step.toLocaleString()}${suffixOf(entry)}`,
    keepsOptimizer: entry.keeps_optimizer,
    testid: `tlp-from-${entry.step}`,
  })),
);

function suffixOf(entry) {
  if (entry.final) return " · final";
  const newest = options.value.steps[0];
  return entry === newest && options.value.status !== "completed"
    ? " · where it stopped"
    : "";
}

const endsAt = computed(() => {
  const end = (fromStep.value ?? 0) + more.value;
  const perStep = options.value?.seconds_per_step;
  const minutes = perStep ? Math.max(1, Math.round((more.value * perStep) / 60)) : null;
  return `Ends at step ${end.toLocaleString()}${minutes ? ` · about ${minutes} min` : ""}`;
});

const canStart = computed(
  () =>
    !!options.value &&
    !options.value.reason &&
    fromStep.value != null &&
    Number.isInteger(more.value) &&
    more.value >= 1,
);

function typed(value) {
  more.value = Math.floor(Number(value));
}

onMounted(async () => {
  try {
    const answer = await getTrainLongerOptions(props.jobId);
    const newest = answer.steps[0]?.step ?? null;
    fromStep.value = newest;
    // A finished run gets a round thousand; a stopped or failed one what is
    // left of the target it was given.
    const left = (answer.total_steps ?? 0) - (newest ?? 0);
    more.value = answer.status !== "completed" && left > 0 ? left : FINISHED_MORE;
    options.value = answer;
  } catch (err) {
    options.value = {
      steps: [],
      reason: errorDetail(err) || "Could not read the steps this run kept.",
    };
  }
});

async function start() {
  if (!canStart.value || starting.value) return;
  starting.value = true;
  try {
    const answer = await trainJobLonger(props.jobId, fromStep.value, more.value);
    emit("started", answer);
    emit("close");
  } catch (err) {
    useNoticeStore().push({
      level: "error",
      text: errorDetail(err) || "Could not start training it longer.",
    });
  } finally {
    starting.value = false;
  }
}
</script>

<style scoped>
.tlp {
  width: 340px;
  max-width: calc(100vw - 2 * var(--space-5));
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.tlp-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.tlp-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.tlp-meta,
.tlp-hint,
.tlp-scale {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.tlp-meta {
  align-self: center;
}

.tlp-steps {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.tlp-range {
  flex: 1;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.tlp-number {
  width: 96px;
}

.tlp-scale {
  display: flex;
  justify-content: space-between;
  margin-right: calc(96px + var(--space-4));
}

.tlp-trade {
  margin: 0;
  line-height: 1.45;
}

.tlp-note {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
}

.tlp-acts {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}
</style>
