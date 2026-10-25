<template>
  <div class="tbm tb-train-panel">
    <span class="tbm-caret tbm-caret--icon-center-end"></span>
    <div class="tbm-header">
      <v-icon size="18" class="tbm-header-icon">mdi-school-outline</v-icon>
      <span class="tbm-title"
        >Train a LoRA on {{ store.scopeName || "this set" }}
        <span class="tb-train-count"
          >({{ store.scopePictureCount }} picture{{
            store.scopePictureCount === 1 ? "" : "s"
          }})</span
        ></span
      >
    </div>

    <div class="tbm-section tb-train-body">
      <!-- ai-toolkit not configured: block start, link to settings -->
      <div v-if="store.configLoaded && !store.aiToolkitConfigured" class="tb-train-warn">
        <v-icon size="15" class="mr-1">mdi-alert-outline</v-icon>
        <span>
          ai-toolkit isn't set up yet.
          <button type="button" class="tb-train-link" @click="goToSettings">
            Set up ai-toolkit first
          </button>
        </span>
    </div>

    <!-- Base model: a training recipe -->
    <v-select
      v-model="store.selectedPreset"
      :items="presetItems"
      item-title="title"
      item-value="value"
      item-props="props"
      label="Base model"
      density="comfortable"
      :loading="store.presetsLoading"
      hide-details="auto"
    />

    <!-- What it learns: asked only of a set; a character is a person -->
    <v-select
      v-if="store.scopeType !== 'character'"
      v-model="store.subject"
      :items="SUBJECT_ITEMS"
      label="What is this set of?"
      density="comfortable"
      hide-details="auto"
      :error-messages="subjectError ? [subjectError] : []"
    />

    <v-select
      v-model="store.effort"
      :items="EFFORT_ITEMS"
      label="How hard to train"
      density="comfortable"
      hide-details
    />

    <v-select
      v-model="store.fit"
      :items="FIT_ITEMS"
      label="Fit to this GPU"
      density="comfortable"
      hide-details
    />

    <!-- Trigger word -->
    <v-text-field
      v-model="triggerModel"
      label="Trigger word"
      density="comfortable"
      hide-details="auto"
      :error-messages="triggerError ? [triggerError] : []"
    />

    <!-- Advanced -->
    <v-expansion-panels variant="accordion" class="tb-train-adv">
      <v-expansion-panel>
        <v-expansion-panel-title>Advanced settings</v-expansion-panel-title>
        <v-expansion-panel-text>
          <p class="tb-train-adv-note">
            Leave a field blank to use the value the recipe works out from
            your choices, the picture count and this GPU.
          </p>
          <div class="tb-train-adv-grid">
            <v-text-field
              v-model.number="store.steps"
              label="Steps"
              placeholder="from the recipe"
              type="number"
              min="1"
              density="compact"
              hide-details="auto"
              :error-messages="stepsError ? [stepsError] : []"
            />
            <v-text-field
              v-model.number="store.networkRank"
              label="Network rank (linear)"
              placeholder="from the recipe"
              type="number"
              min="1"
              density="compact"
              hide-details
            />
            <v-text-field
              v-model.number="store.networkAlpha"
              label="Network alpha"
              placeholder="from the recipe"
              type="number"
              min="1"
              density="compact"
              hide-details
            />
            <v-text-field
              v-model="store.learningRate"
              label="Learning rate"
              placeholder="from the recipe"
              density="compact"
              hide-details
            />
            <v-text-field
              v-model.number="store.batchSize"
              label="Batch size"
              placeholder="from the recipe"
              type="number"
              min="1"
              density="compact"
              hide-details
            />
            <v-text-field
              v-model.number="store.saveEvery"
              label="Save every (steps)"
              placeholder="from the recipe"
              type="number"
              min="1"
              density="compact"
              hide-details
            />
          </div>
          <v-textarea
            v-model="store.samplePrompts"
            label="Sample prompts (one per line)"
            rows="2"
            auto-grow
            density="compact"
            hide-details
            class="mt-2"
          />
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>

    <p class="tb-train-note">
      <v-icon size="14" class="mr-1">mdi-information-outline</v-icon>
      This pauses other GPU work while it runs.
    </p>

    <div v-if="store.submitError" class="tb-train-error">
      {{ store.submitError }}
    </div>

    <v-btn
      color="primary"
      block
      :loading="store.submitting"
      :disabled="!canStart"
      @click="onStart"
    >
      Start training
    </v-btn>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useTrainingStore } from "../../stores/useTrainingStore";

const emit = defineEmits(["started"]);

const store = useTrainingStore();

const triggerError = ref("");
const stepsError = ref("");
const subjectError = ref("");

const SUBJECT_ITEMS = [
  { title: "A person", value: "person" },
  { title: "A style", value: "style" },
  { title: "A thing", value: "thing" },
];
const EFFORT_ITEMS = [
  { title: "Gentle", value: "gentle" },
  { title: "Standard", value: "standard" },
  { title: "Strong", value: "strong" },
];
const FIT_ITEMS = [
  { title: "Auto", value: "auto" },
  { title: "Save memory", value: "save_memory" },
  { title: "Best quality", value: "best_quality" },
];

// Fetch recipes / config the first time the menu opens.
watch(
  () => store.trainMenuOpen,
  (isOpen) => {
    if (!isOpen) return;
    store.fetchPresets();
    if (!store.configLoaded) store.fetchConfig();
    triggerError.value = "";
    stepsError.value = "";
    subjectError.value = "";
    store.submitError = "";
  },
);

// A recipe the checkout has no model code for is listed but cannot be picked.
const presetItems = computed(() =>
  (store.presets || []).map((p) => ({
    title: p.offered ? p.label : `${p.label} (needs a newer ai-toolkit)`,
    value: p.id,
    props: { disabled: !p.offered },
  })),
);

const triggerModel = computed({
  get: () => store.triggerWord,
  set: (v) => {
    store.triggerWord = v;
  },
});

const canStart = computed(
  () =>
    store.aiToolkitConfigured &&
    store.scopeId != null &&
    !store.submitting,
);

function validate() {
  triggerError.value = "";
  stepsError.value = "";
  subjectError.value = "";
  let ok = true;
  if (!String(store.triggerWord || "").trim()) {
    triggerError.value = "Trigger word is required.";
    ok = false;
  }
  const steps = store.steps;
  if (steps !== null && steps !== "" && !(Number(steps) > 0)) {
    stepsError.value = "Steps must be greater than 0.";
    ok = false;
  }
  if (!store.subject) {
    subjectError.value = "Say what the LoRA should learn.";
    ok = false;
  }
  return ok;
}

async function onStart() {
  if (!validate()) return;
  try {
    const job = await store.startTraining();
    store.trainMenuOpen = false;
    // The create response carries the resolver's result as an object.
    const profile = job?.resolved_profile?.profile_label;
    store.showQueuedToast(
      profile
        ? `Training queued: ${profile}. Watch progress in the Tasks tab.`
        : "Training queued. Watch progress in the Tasks tab.",
    );
    emit("started");
  } catch {
    // store.submitError is already set and shown inline.
  }
}

function goToSettings() {
  store.trainMenuOpen = false;
  // Bump the deep-link request. App.vue opens the settings dialog and
  // UserSettingsDialog selects the Training tab (both watch this counter).
  store.requestTrainingSettings();
}
</script>

<style scoped>
.tb-train-panel {
  min-width: 320px;
  max-width: 380px;
}

.tb-train-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.tb-train-count {
  font-weight: 400;
  opacity: 0.7;
}

.tb-train-warn {
  display: flex;
  align-items: flex-start;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-warning));
  background: rgba(var(--v-theme-warning), 0.1);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
}

.tb-train-link {
  background: none;
  border: none;
  padding: 0;
  color: rgb(var(--v-theme-primary));
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
}

.tb-train-link:focus-visible {
  outline: var(--focus-width) solid rgb(var(--v-theme-primary));
  outline-offset: var(--focus-offset);
  border-radius: var(--radius-sm);
}

.tb-train-adv :deep(.v-expansion-panel-title) {
  min-height: 40px;
  font-size: var(--text-sm);
}

.tb-train-adv-note {
  font-size: var(--text-2xs);
  opacity: 0.6;
  margin: 0 0 var(--space-3);
}

.tb-train-adv-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.tb-train-note {
  display: flex;
  align-items: center;
  font-size: var(--text-xs);
  opacity: 0.75;
  margin: 0;
}

.tb-train-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
}
</style>

